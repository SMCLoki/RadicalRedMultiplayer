-- Radical Red Online: PvP battle engine (the game side of the lockstep protocol).
-- Loaded by rr_mp.lua (dofile); defines _RRMP_BATTLE_NEW. Must not end with a return statement.
-- Design and evidence: docs/phase4_battle.md. Addresses: docs/ram_map.md "Battle" (verified on RR) and
-- pret/pokefirered (symbols branch) for the rest.
--
-- Each client runs a normal single battle in which the local player is battler 0 and the other player is the
-- "opponent", battler 1:
-- * start: gEnemyParty = their team, flags TRAINER | EREADER_TRAINER (no trainer party is generated, no EXP,
--   no items, the opponent's name comes from the e-reader trainer slot in SaveBlock2, which we fill with their
--   name), savedCallback = CB2_ReturnToField (no black-out on a loss), callback2 = CB2_InitBattle.
-- * decision rounds: every time the game asks battler 0 or 1 for a choice (action/move/switch at turn start,
--   or a replacement after a faint or U-turn) is one protocol "turn". Battler 0 is the local player using the
--   normal menus; we read the result. Battler 1's controller is the AI; we hold it ("stall": keep its
--   gBattleControllerExecFlags bit set so the main loop waits and the AI just re-runs) until the other
--   player's choice arrives, then overwrite its reply in gBattleBufferB before the main loop reads it.
-- * RNG: VBlankCB_Battle calls Random() every frame and animations (drawn on different sides on the two
--   machines) take different numbers of frames, so time-based seeding drifts (real mGBA run 5). Instead the
--   random value is keyed to the game's logical position: at the end of every frame in which the battle script
--   may run (gBattleControllerExecFlags == 0, the RunBattleScriptCommands gate), gRngValue is set from
--   (seed, round, gBattleMainFunc, gBattlescriptCurrInstr, how often that position was reached this round).
--   Visual code still gets random numbers while controllers animate. A per-round state hash detects desyncs.
-- * end: gBattleOutcome; afterwards the party (rr_mp backup), money, battle-style option and e-reader slot are
--   restored once the game is back on the map.

function _RRMP_BATTLE_NEW(env)
  local E = { cur = nil }
  local log = env.event or function() end
  local cfg = env.cfg or {}

  local A = {
    gMain = 0x030030F0, battleFlags = 0x02022B4C, mainFunc = 0x03004F84, execFlags = 0x02023BC8,
    bufA = 0x02022BC4, bufB = 0x020233C4, mons = 0x02023BE4, comm = 0x02023E82, outcome = 0x02023E8A,
    chosenAction = 0x02023D7C, rng = 0x03005000,
    -- RR's battle code (crit, damage roll) draws from a second LCG, x*1103515245+12345, one step per call and never
    -- per frame (found in real mGBA run 10: gRngValue moved only by the VBlank step during adjustnormaldamage)
    rng2 = tonumber(cfg.rng2_addr or "") or 0x020386D0, enemyParty = 0x0202402C, enemyCount = 0x0202402A,
    trainerA = 0x020386AE, sb1Ptr = 0x03005008, sb2Ptr = 0x0300500C, objEvents = 0x02036E38,
    battleStructPtr = 0x02023FE8, scriptInstr = 0x02023D74, controllerFuncs = 0x03004FE0,
  }
  local BS_MON_TO_SWITCH = tonumber(cfg.bs_mon_to_switch or "") or 0x5C  -- gBattleStruct->monToSwitchIntoId
  local CB_OVERWORLD = tonumber(cfg.cb_overworld or "") or 0x080565B5
  local CB_INIT_BATTLE = tonumber(cfg.cb_init_battle or "") or 0x0800FD9D
  local CB_RETURN = tonumber(cfg.cb_return_to_field or "") or 0x080567DD
  local MAIN_SELECT = 0x08014041                     -- HandleTurnActionSelectionState
  local CMD_ACTION, CMD_MOVE, CMD_POKEMON, CMD_TWO, CMD_MON = 18, 20, 22, 33, 34
  local FLAGS = tonumber(cfg.pvp_flags or "") or (0x08 | 0x800)  -- TRAINER | EREADER_TRAINER
  local MON, BMON, BUF = 100, 0x58, 0x200
  local SB1_MONEY, SB2_OPTIONS, SB2_EREADER, EREADER_LEN = 0x290, 0x15, 0x4A0, 0xBC
  E.addr = A

  local function r8(a) return emu:read8(a) end
  local function r16(a) return emu:read16(a) end
  local function r32(a) return emu:read32(a) end
  local function w8(a, v) emu:write8(a, v) end
  local function w32(a, v) emu:write32(a, v) end

  local function writeHex(addr, hex)
    local n = #hex // 2
    for i = 0, n - 1 do w8(addr + i, tonumber(hex:sub(i * 2 + 1, i * 2 + 2), 16)) end
  end

  local function tohex(s) return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end)) end

  local function backupPath() return env.work .. "mp_battle_backup_" .. tostring(env.profile) .. ".txt" end

  local function readBackup()
    local f = io.open(backupPath(), "rb")
    if not f then return nil end
    local money, options, ereader = f:read("l"), f:read("l"), f:read("l")
    f:close()
    if not (tonumber(money) and tonumber(options) and ereader) then return nil end
    return { money = tonumber(money), options = tonumber(options), ereader = ereader }
  end

  -- Gen 3 text encoding for the opponent's name. The game shows only 5 characters for an e-reader trainer
-- (CopyEReaderTrainerName5), so "Player2" appears as "Playe": a hard limit of this battle type.
  local function encodeName(name)
    local out = {}
    for i = 1, math.min(#name, 5) do
      local c = name:byte(i)
      if c >= 65 and c <= 90 then out[#out + 1] = 0xBB + c - 65
      elseif c >= 97 and c <= 122 then out[#out + 1] = 0xD5 + c - 97
      elseif c >= 48 and c <= 57 then out[#out + 1] = 0xA1 + c - 48
      elseif c == 32 then out[#out + 1] = 0x00
      else out[#out + 1] = 0xAC end
    end
    while #out < 8 do out[#out + 1] = 0xFF end
    return out
  end

  ------------------------------------------------------------------------------------------------
  -- start / end

  local function inOverworld()
    if r32(A.battleFlags) ~= 0 or r32(A.gMain + 4) ~= CB_OVERWORLD then return false end
    local flags = r32(A.objEvents)
    if flags & 0x10001 ~= 0x10001 then return false end            -- player object active
    if flags & 0x100 ~= 0 then return false end                    -- frozen: menu, text box, script
    return not (flags & 0x40 ~= 0 and flags & 0x80 == 0)           -- not mid-step
  end
  E.inOverworld = inOverworld

  local function startBattle(c)
    local b = c.b
    local sb1, sb2 = r32(A.sb1Ptr), r32(A.sb2Ptr)
    -- backups that live in the save blocks (the party is backed up by rr_mp)
    c.backup = {
      money = r32(sb1 + SB1_MONEY),
      options = r8(sb2 + SB2_OPTIONS),
      ereader = tohex(emu:readRange(sb2 + SB2_EREADER, EREADER_LEN)),
    }
    local f = io.open(backupPath(), "wb")
    if f then f:write(c.backup.money, "\n", c.backup.options, "\n", c.backup.ereader, "\n"); f:close() end
    -- opponent party
    local party = b.opp and b.opp.party or {}
    for i = 0, 5 do
      local hex = party[i + 1]
      if type(hex) == "string" and #hex == 200 then writeHex(A.enemyParty + i * MON, hex)
      else for o = 0, MON - 4, 4 do w32(A.enemyParty + i * MON + o, 0) end end
    end
    w8(A.enemyCount, math.min(#party, 6))
    -- opponent name + class in the e-reader trainer slot; battle style SET (no "switch?" prompt that only one
    -- side would see)
    local name = encodeName(tostring(b.opp and b.opp.name or "Rival"))
    for i = 1, 8 do w8(sb2 + SB2_EREADER + 4 + i - 1, name[i]) end
    -- class: FACILITY_CLASS_RED (134) / LEAF (135) by the opponent's sprite (OBJ_EVENT_GFX_GREEN_NORMAL = 7);
    -- the save's leftover value showed "Rocket Admin" on RR. pvp_class overrides if RR's tables differ.
    local class = tonumber(cfg.pvp_class or "") or ((b.oppGfx == 7) and 135 or 134)
    w8(sb2 + SB2_EREADER + 1, class)
    w8(sb2 + SB2_OPTIONS, c.backup.options | 0x02)
    -- go
    emu:write16(A.trainerA, 0)
    w8(A.outcome, 0)
    w32(A.rng, b.seed or 0)
    if A.rng2 ~= 0 then w32(A.rng2, (b.seed or 0) ~ 0x5EED2222) end
    w32(A.battleFlags, FLAGS)
    w32(A.gMain + 8, CB_RETURN)
    w32(A.gMain + 4, CB_INIT_BATTLE)
    c.phase, c.startedAt = "battle", env.frame()
    log("battle: starting in-game battle")
  end

  local function restoreAll(c)
    if c.backup then
      local sb1, sb2 = r32(A.sb1Ptr), r32(A.sb2Ptr)
      w32(sb1 + SB1_MONEY, c.backup.money)
      w8(sb2 + SB2_OPTIONS, c.backup.options)
      writeHex(sb2 + SB2_EREADER, c.backup.ereader)
    end
    os.remove(backupPath())
    if env.restoreParty then env.restoreParty() end
    log("battle: party, money and options restored")
  end

  ------------------------------------------------------------------------------------------------
  -- decision rounds

  -- random value for the next logic step, from where the battle logically is (not when)
  local function logicSeed(c, roundId, main, instr, occ)
    local h = 0x811C9DC5
    for _, v in ipairs({ c.b.seed or 0, roundId, main, instr, occ }) do
      for i = 0, 3 do h = ((h ~ ((v >> (8 * i)) & 0xFF)) * 0x01000193) & 0xFFFFFFFF end
    end
    return h
  end

  -- LCG helpers: gRngValue is x*0x41C64E6D+0x6073, RR's second battle RNG is x*0x41C64E6D+12345 (same multiplier,
  -- whose inverse mod 2^32 is 0xEEB9EB65)
  local function rngNext(x) return (x * 0x41C64E6D + 0x6073) & 0xFFFFFFFF end
  local function rngPrev(x) return ((x - 0x6073) * 0xEEB9EB65) & 0xFFFFFFFF end
  local function rng2Next(x) return (x * 0x41C64E6D + 12345) & 0xFFFFFFFF end
  local function rng2Prev(x) return ((x - 12345) * 0xEEB9EB65) & 0xFFFFFFFF end
  local TURN_ORDER = tonumber(cfg.turn_order_func or "") or 0x080150A9   -- SetActionsAndBattlersTurnOrder

  local function lockRng(c, main)
    local instr = r32(A.scriptInstr)
    local rid = c.roundId or 0
    local key = rid .. ":" .. main .. ":" .. instr
    if key ~= c.rngKey then
      c.rngKey = key
      if c.rngRound ~= rid then c.occ, c.rngRound = {}, rid end
      c.occ[key] = (c.occ[key] or 0) + 1
      c.rngOcc = c.occ[key]
    end
    if r32(A.execFlags) == 0 then
      local seed = logicSeed(c, rid, main, instr, c.rngOcc)
      local seed2 = seed ~ 0x5EED2222
      if main == TURN_ORDER and c.b.side == 1 then
        -- Speed ties are decided by the first Random() & 1 of the turn-order step, but each game is battler 0 for
        -- its own player, so the same bit would let a different player move first on each machine (real mGBA run
        -- 11). On side 1, start from the seed whose first output has bit 0 flipped: the mirrored battler wins the
        -- tie, i.e. the same player. Done for both generators; nothing else in this step should draw randoms.
        seed = rngPrev(rngNext(seed) ~ 0x10000)
        seed2 = rng2Prev(rng2Next(seed2) ~ 0x10000)
      end
      w32(A.rng, seed)
      if A.rng2 ~= 0 then w32(A.rng2, seed2) end
    end
  end

  local function stateHash(c)
    -- side-canonical: side 0's active mon first on both machines
    local order = c.b.side == 0 and { 0, 1 } or { 1, 0 }
    local h = 0x811C9DC5
    local function mix(v, n)
      for i = 0, n - 1 do h = ((h ~ ((v >> (8 * i)) & 0xFF)) * 0x01000193) & 0xFFFFFFFF end
    end
    for _, battler in ipairs(order) do
      local m = A.mons + battler * BMON
      mix(r16(m), 2)                       -- species
      mix(r16(m + 0x28), 2)                -- hp
      mix(r16(m + 0x2C), 2)                -- max hp
      mix(r32(m + 0x24), 4)                -- pp[4]
      mix(r32(m + 0x18), 4); mix(r32(m + 0x1C), 4)  -- stat stages
      mix(r32(m + 0x4C), 4)                -- status1
    end
    return string.format("%08x", h)
  end

  local function newRound(c, kind, askedLocal, askedRemote)
    local rd = { id = c.nextRound, kind = kind, askedLocal = askedLocal, askedRemote = askedRemote,
                 hash = stateHash(c) }
    c.nextRound = c.nextRound + 1
    c.roundId = rd.id
    if kind == "turn" then
      -- bytes 4-7 of battler 0's reply are the Mega/Ultra Burst/Z-Move/Dynamax flags. They are not cleared by
      -- the game and hold leftovers from battle setup (98 00 0A 00 seen in real mGBA run 10), which were sent to
      -- the other game as "use Z-Move". Clear them so only a fresh EmitMoveChosen can set them.
      w32(A.bufB + 4, 0)
    end
    if not askedLocal then rd.decision = { k = "n" } end
    c.round = rd
    c.rounds[rd.id] = rd
    return rd
  end

  local function remoteFor(c, rd) return c.remote[rd.id] end

  -- write battler 1's reply for the stage the main loop is about to read
  -- CFRU's move reply is 8 bytes (EmitMoveChosen): {0x21, 10, slot, target, mega, ultraBurst, zMove, dynamax},
  -- and mega.c ORs bytes 4-7 into gNewBS's per-battler "use it" flags. Always write all 8 bytes, or stale bytes
  -- left in the buffer turn the remote player's move into a Z-Move/Mega/Dynamax (real mGBA run 8).
  local function gimmickBytes(g)
    g = tonumber(g) or 0
    return (g & 1) | (((g >> 1) & 1) << 8) | (((g >> 2) & 1) << 16) | (((g >> 3) & 1) << 24)
  end

  local function writeRemoteReply(stage, d)
    local b1 = A.bufB + BUF
    if stage == "action" then
      w32(b1, CMD_TWO | ((d.k == "s" and 2 or 0) << 8))
      w32(b1 + 4, 0)
    elseif stage == "case" then
      if d.k == "s" then w32(b1, CMD_MON | ((d.i or 0) << 8)); w32(b1 + 4, 0)
      else
        local target = (d.t == 0) and 1 or 0     -- their battler 0 is our battler 1 and vice versa
        w32(b1, CMD_TWO | (10 << 8) | (((d.s or 0) & 3) << 16) | (target << 24))
        w32(b1 + 4, gimmickBytes(d.g))
      end
    elseif stage == "replace" then
      w32(b1, CMD_MON | ((d.i or 0) << 8))
      -- the AI controller also stores its pick here directly; the script copies from the buffer, but keep both equal
      local bs = r32(A.battleStructPtr)
      if bs >= 0x02000000 and bs < 0x02040000 then w8(bs + BS_MON_TO_SWITCH + 1, d.i or 0) end
    end
  end

  local function stall() w32(A.execFlags, r32(A.execFlags) | 0x2) end

  -- Parking battler 1: while its controller function is BattleControllerDummy (a bare `bx lr`), a command
  -- sent to it is never answered, so the main loop / battle script waits on its own - no race with the frame
  -- callback, and CFRU's AI (temporary Mega Evolution, damage predictions, switch/item state) never runs for
  -- the choices we answer ourselves. Falls back to stall() if the dummy isn't where vanilla FR has it.
  local DUMMY = tonumber(cfg.controller_dummy or "") or 0x0802E311   -- controller_dummy=0 disables parking
  local dummyOk   -- checked on first use: the routine must be a bare `bx lr`
  local function dummyAvailable()
    if dummyOk == nil then
      dummyOk = DUMMY >= 0x08000000 and DUMMY < 0x0A000000 and emu:read16(DUMMY - 1) == 0x4770
      if not dummyOk then log("battle: BattleControllerDummy not found, using the exec-flag hold") end
    end
    return dummyOk
  end
  local FUNC1 = A.controllerFuncs + 4

  local function park(c)
    if not dummyAvailable() then return false end
    local f = r32(FUNC1)
    if f ~= DUMMY then c.savedFunc1 = f; w32(FUNC1, DUMMY) end
    return true
  end

  local function unpark(c)
    if r32(FUNC1) == DUMMY and c.savedFunc1 then w32(FUNC1, c.savedFunc1) end
  end

  local function parked() return dummyAvailable() and r32(FUNC1) == DUMMY end

  -- answer battler 1's pending command ourselves (what OpponentBufferExecCompleted does after the AI)
  local function answer(stage, d)
    writeRemoteReply(stage, d)
    w32(A.execFlags, r32(A.execFlags) & ~0x2)
  end

  local function isDecision(cmd) return cmd == CMD_ACTION or cmd == CMD_MOVE or cmd == CMD_POKEMON end

  -- Lag: when a frame overruns, mGBA's frame callback fires in the middle of the game's main loop. Writing
  -- battle state then races the game, and the VBlank Random() lands mid-logic. The play-time counter
  -- (SaveBlock2+0x10..0x13) advances once per finished main-loop iteration; if it didn't move since the last
  -- callback, the game is mid-iteration: touch nothing, just cancel the VBlank Random() call(s).
  local VBLANK_STEPS = tonumber(cfg.vblank_rng_steps or "") or 1
  local function rngPrev(x) return ((x - 0x6073) * 0xEEB9EB65) & 0xFFFFFFFF end

  local function lagging(c)
    local pt = r32(r32(A.sb2Ptr) + 0x10)
    local f = env.frame()
    local lag = pt == c.lastPlayTime and f - (c.ptMovedAt or -100) <= 3   -- a stopped timer is not lag
    if pt ~= c.lastPlayTime then c.ptMovedAt = f end
    c.lastPlayTime = pt
    return lag and cfg.lag_guard ~= "0"
  end

  local function battleFrame(c)
    if lagging(c) then
      local x = r32(A.rng)
      for _ = 1, VBLANK_STEPS do x = rngPrev(x) end
      if cfg.rng_lock ~= "0" then w32(A.rng, x) end
      c.lagFrames = (c.lagFrames or 0) + 1
      -- parking is safe even mid-iteration (it only stops the AI from answering), so don't miss the window
      -- before the main loop sends CHOOSEACTION (run 7: one turn in 18 was answered by the AI)
      if not c.aborted and r32(A.mainFunc) == MAIN_SELECT and r8(A.comm + 1) == 0 then park(c) end
      return
    end
    local main = r32(A.mainFunc)
    local exec = r32(A.execFlags)
    local st0, st1 = r8(A.comm), r8(A.comm + 1)
    local cmd0, cmd1 = r8(A.bufA), r8(A.bufA + BUF)
    local rd = c.round

    if c.clearReplyMark then
      -- the game consumed the last round's battler-1 reply during this frame; erase its marker so the
      -- replacement detection below doesn't mistake it for a fresh AI answer
      if r8(A.bufB + BUF) == CMD_MON then w8(A.bufB + BUF, 0) end
      c.clearReplyMark = nil
    end

    -- a new round starts when the game first asks for decisions
    if not rd then
      if main == MAIN_SELECT and c.prevMain ~= MAIN_SELECT then
        rd = newRound(c, "turn", true, true)
      elseif main ~= MAIN_SELECT then
        local askL = exec & 1 ~= 0 and cmd0 == CMD_POKEMON
        -- the AI answers CHOOSEPOKEMON in the same frame it is asked, so the ask shows up as its fresh reply
        -- (byte 0 = CHOSENMONRETURNVALUE); after each round we overwrite that byte so it is not seen twice
        local askR = cmd1 == CMD_POKEMON and (exec & 2 ~= 0 or r8(A.bufB + BUF) == CMD_MON)
        if askL or askR then rd = newRound(c, "replace", askL, askR) end
      end
    end

    if rd then
      local remote = c.aborted and { k = "ai" } or remoteFor(c, rd)
      if remote and remote.k == "ai" then unpark(c) end
      if rd.kind == "turn" then
        -- "Run" in a trainer-style battle makes the game print "can't run away" and ask again, forever. In PvP it
        -- means "give up": forfeit once (both games end the battle) - reported in real play, 2026-10-24.
        if not c.ranAway and r8(A.bufB) == CMD_TWO and r8(A.bufB + 1) == 3 then
          c.ranAway = true
          log("battle: Run = forfeit")
          if env.forfeit then pcall(env.forfeit) end
        end
        -- local choice is final once battler 0's selection state passes "case chosen"
        if not rd.decision and (st0 == 3 or st0 == 4) then
          local act = r8(A.chosenAction)
          if act == 2 then rd.decision = { k = "s", i = r8(A.bufB + 1) }
          elseif r8(A.bufB) ~= CMD_TWO or r8(A.bufB + 1) ~= 10 then
            -- the move reply (0x21, 10, slot, target, gimmicks) isn't in the buffer yet: wait, don't read stale bytes
          else
            -- g: the local player's Mega / Ultra Burst / Z-Move / Dynamax choice (CFRU reply bytes 4-7)
            local g = 0
            for i = 0, 3 do if r8(A.bufB + 4 + i) ~= 0 then g = g | (1 << i) end end
            rd.decision = { k = "m", s = r8(A.bufB + 2), t = r8(A.bufB + 3), g = g }
          end
        end
        -- battler 1: park before the main loop sends CHOOSEACTION, answer each stage with the remote choice
        if st1 == 0 and not (remote and remote.k == "ai") then park(c) end
        if (st1 == 1 or st1 == 2) and not (remote and remote.k == "ai") then
          local stage = st1 == 1 and "action" or "case"
          if parked() and exec & 2 ~= 0 and isDecision(cmd1) then
            if remote then
              answer(stage, remote)
              if st1 == 2 then unpark(c) end
            end
          elseif not remote then
            stall()                                -- fallback: no dummy, or the park window was missed
          else
            writeRemoteReply(stage, remote)
          end
        end
        if st1 >= 3 then unpark(c) end             -- standby message etc. need the real controller
        if main ~= MAIN_SELECT and c.prevMain == MAIN_SELECT then
          unpark(c)
          c.round = nil
          c.clearReplyMark = true
        end
      else -- replacement after a faint / U-turn
        if rd.askedLocal and not rd.decision and exec & 1 == 0 then
          rd.decision = { k = "r", i = r8(A.bufB + 1) }
        end
        if rd.askedRemote and not rd.applied then
          if not remote then
            -- the AI answered once already; hold the script by marking battler 1 busy again
            if park(c) then w32(A.execFlags, r32(A.execFlags) | 0x2) else stall() end
          else
            if remote.k == "r" then writeRemoteReply("replace", remote) end
            w32(A.execFlags, r32(A.execFlags) & ~0x2)
            unpark(c)
            rd.applied = true
          end
        end
        if (not rd.askedLocal or rd.decision) and (not rd.askedRemote or rd.applied) then
          c.round = nil
          c.clearReplyMark = true
        end
      end
    elseif parked() and not isDecision(cmd1) then
      unpark(c)                                    -- never leave battler 1 parked outside a decision
    end
    c.prevMain = main
    if cfg.rng_lock ~= "0" then
      lockRng(c, main)
      -- also cancel the VBlank Random() that follows this callback (run 6: the callback runs before the VBlank
      -- handler), so gRngValue never moves with time - only with game code
      if cfg.vblank_cancel ~= "0" then
        local x = r32(A.rng)
        for _ = 1, VBLANK_STEPS do x = rngPrev(x) end
        w32(A.rng, x)
      end
    end

    local outcome = r8(A.outcome)
    if outcome ~= 0 and c.winner == nil then
      if outcome == 1 then c.winner = c.b.side
      elseif outcome == 2 then c.winner = 1 - c.b.side
      else c.winner = false end
    end
  end
  E.unparkAll = function() if E.cur then unpark(E.cur) end end

  ------------------------------------------------------------------------------------------------
  -- rr_mp engine interface

  function E.begin(b)
    local c = { b = b, phase = "waiting", requestedAt = env.frame(), rounds = {}, remote = {},
                nextRound = b.turn or 1, prevMain = 0 }
    E.cur = c
    if b.resumed and r32(A.battleFlags) ~= 0 then
      -- script reloaded mid-battle: attach to the running battle
      c.phase, c.seenBattle, c.startedAt, c.backup = "battle", true, env.frame(), readBackup()
      c.prevMain = r32(A.mainFunc)
    end
    return true
  end

  function E.localAction(b)
    local c = E.cur
    if not c or c.b ~= b then return nil end
    local rd = c.rounds[b.turn]
    return rd and rd.decision or nil
  end

  function E.stateHash(b)
    local c = E.cur
    local rd = c and c.rounds[b.turn]
    return rd and rd.hash or nil
  end

  function E.applyTurn(b, turn, actions)
    local c = E.cur
    if not c or type(actions) ~= "table" then return end
    local theirs = actions[(b.side == 0) and 2 or 1]
    if type(theirs) == "table" then c.remote[turn] = theirs end
  end

  function E.result(b)
    local c = E.cur
    if not c or c.winner == nil then return nil end
    if c.winner == false then return env.null end
    return c.winner
  end

  -- called on battle_result; returns true: rr_mp must NOT restore the party now (we do it on the map)
  function E.finish(b, winner)
    local c = E.cur
    if not c then return false end
    if c.phase == "waiting" then E.cur = nil; os.remove(backupPath()); return false end
    c.aborted = true
    E.unparkAll()
    if r8(A.outcome) == 0 and r32(A.battleFlags) ~= 0 then
      -- the battle ended on the server (forfeit, disconnect, desync, timer): end it in the game too
      w8(A.outcome, (winner == b.side) and 1 or 2)
    end
    c.phase = "ending"
    return true
  end

  function E.tick(frame)
    local c = E.cur
    if not c then return end
    if c.phase == "waiting" then
      if inOverworld() then startBattle(c)
      elseif frame - c.requestedAt > 600 and frame % 300 == 0 then
        log("battle: waiting until you stand still on the map (close menus, finish walking)")
      end
    elseif c.phase == "battle" or c.phase == "ending" then
      if r32(A.battleFlags) ~= 0 then
        battleFrame(c)
        c.seenBattle = true
      elseif c.seenBattle and r32(A.gMain + 4) == CB_OVERWORLD then
        restoreAll(c)
        E.cur = nil
      elseif not c.seenBattle and frame - c.startedAt > 600 then
        log("battle: the game did not enter the battle; restoring")
        restoreAll(c)
        E.cur = nil
      end
    end
  end

  function E.status()
    local c = E.cur
    if not c then return nil end
    local rd = c.round
    return string.format("engine %s round %s%s", c.phase, rd and rd.id or "-",
      rd and (rd.askedRemote and not remoteFor(c, rd) and " (waiting for opponent)" or "") or "")
  end

  return E
end
