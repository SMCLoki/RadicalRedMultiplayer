"""A tiny model of the FireRed overworld engine, running inside tools/mgba_mock.FakeGBA memory.

It implements just what client/rr_visuals.lua relies on, following pret/pokefirered:
- gMapHeader.events -> MapEvents in ROM; LoadObjEventTemplatesFromHeader copies templates to SaveBlock1+0x8E0
- TrySpawnObjectEvents / RemoveObjectEventsOutsideView on every camera move (player step), using
  gMapHeader.events->objectEventCount and the SaveBlock1 template array
- object events (0x24 bytes) with sprites in gSprites (0x44 bytes, data[0] = object index)
- held movements: movementActionId + heldMovementActive/Finished + sprite data[2], for the FACE, WALK_NORMAL
  and WALK_FAST actions (16 / 8 frames per tile; coords update at the start of a step, like the game)

It is a test double, not an emulator: passing tests here means the script's logic and memory writes are
consistent with the decomp, not that the real ROM behaves identically.
"""
from __future__ import annotations

SB1, HDR, OBJ, SPR, AVATAR, GMAIN, SB1PTR = 0x0202552C, 0x02036DFC, 0x02036E38, 0x0202063C, 0x02037078, 0x030030F0, 0x03005008
OBJ_SIZE, SPR_SIZE, TPL_SIZE, NOBJ, NSPR = 0x24, 0x44, 0x18, 16, 64
MAP_OFFSET, MAP_OFFSET_W, MAP_OFFSET_H = 7, 15, 14
DIRS = {1: (0, 1), 2: (0, -1), 3: (-1, 0), 4: (1, 0)}


class FakeOverworld:
    def __init__(self, gba):
        self.g = gba
        self.r, self.w = gba.py.read, gba.py.write
        self.maps: dict[tuple, int] = {}
        self.rom_next = 0x08000100
        self.anim: dict[int, dict] = {}
        self.actions: list[tuple] = []   # (localId, actionId) for every held movement the engine started
        self.mem = gba.mem
        self.w(GMAIN + 4, 4, 0x08011101)   # callback2 = "CB2_Overworld"
        self.w(GMAIN, 4, 0x080123E5)
        self.w(SB1PTR, 4, SB1)
        gba.pre_frame.append(self.step)

    # -- memory helpers --------------------------------------------------
    def s16(self, a):
        v = self.r(a, 2)
        return v - 0x10000 if v >= 0x8000 else v

    def ws16(self, a, v):
        self.w(a, 2, v & 0xFFFF)

    def zero(self, a, n):
        for i in range(0, n, 4):
            self.w(a + i, 4, 0)

    # -- maps ------------------------------------------------------------
    def define_map(self, group, num, npcs=()):
        """npcs: list of (localId, graphicsId, x, y)"""
        events = self.rom_next
        templates = events + 0x20
        self.rom_next = templates + max(1, len(npcs)) * TPL_SIZE + 0x20
        self.w(events, 1, len(npcs))
        self.w(events + 4, 4, templates)
        for i, (lid, gfx, x, y) in enumerate(npcs):
            t = templates + i * TPL_SIZE
            self.w(t, 1, lid)
            self.w(t + 1, 1, gfx)
            self.ws16(t + 4, x)
            self.ws16(t + 6, y)
            self.w(t + 8, 1, 3)
        self.maps[(group, num)] = events

    def load_map(self, group, num, x, y, gfx=0):
        events = self.maps[(group, num)]
        self.w(HDR, 4, 0x08000F00)           # mapLayout
        self.w(HDR + 4, 4, events)
        self.ws16(SB1, x)
        self.ws16(SB1 + 2, y)
        self.w(SB1 + 4, 1, group)
        self.w(SB1 + 5, 1, num)
        count = self.r(events, 1)
        tp = self.r(events + 4, 4)
        for i in range(count):
            for off in range(0, TPL_SIZE, 4):
                self.w(SB1 + 0x8E0 + i * TPL_SIZE + off, 4, self.r(tp + i * TPL_SIZE + off, 4))
        for i in range(NOBJ):
            self.zero(OBJ + i * OBJ_SIZE, OBJ_SIZE)
        for i in range(NSPR):
            self.zero(SPR + i * SPR_SIZE, SPR_SIZE)
        self.anim.clear()
        # player object 0 + sprite 0
        o = OBJ
        self.w(o, 4, 0x1 | 0x8000 | 0x10000)   # active, trackedByCamera, isPlayer
        self.w(o + 4, 1, 0)
        self.w(o + 5, 1, gfx)
        self.w(o + 8, 1, 0xFF)
        self.w(o + 9, 1, num)
        self.w(o + 10, 1, group)
        self.w(o + 0x0B, 1, 0x33)
        for off in (0x0C, 0x10, 0x14):
            self.ws16(o + off, x + MAP_OFFSET)
            self.ws16(o + off + 2, y + MAP_OFFSET)
        self.w(o + 0x18, 1, 0x11)
        self.w(o + 0x1C, 1, 0xFF)
        self._init_sprite(0, 0, x + MAP_OFFSET, y + MAP_OFFSET)
        self.w(AVATAR, 1, 1)
        self.camera_update()

    def _init_sprite(self, sid, obj_index, ox, oy):
        s = SPR + sid * SPR_SIZE
        self.zero(s, SPR_SIZE)
        self.w(s + 0x1C, 4, 0x08000001)
        self.ws16(s + 0x20, (ox - self.s16(SB1) - MAP_OFFSET) * 16 + 120)
        self.ws16(s + 0x22, (oy - self.s16(SB1 + 2) - MAP_OFFSET) * 16 + 80)
        self.ws16(s + 0x2E, obj_index)
        self.w(s + 0x3E, 2, 0x3)   # inUse, coordOffsetEnabled

    # -- engine ----------------------------------------------------------
    def view(self):
        px, py = self.s16(SB1), self.s16(SB1 + 2)
        return px - 2, px + MAP_OFFSET_W + 2, py, py + MAP_OFFSET_H + 2

    def camera_update(self):
        self.try_spawn()
        self.remove_outside_view()

    def try_spawn(self):
        events = self.r(HDR + 4, 4)
        count = self.r(events, 1)
        left, right, top, bottom = self.view()
        group, num = self.r(SB1 + 4, 1), self.r(SB1 + 5, 1)
        for i in range(count):
            t = SB1 + 0x8E0 + i * TPL_SIZE
            lid, gfx = self.r(t, 1), self.r(t + 1, 1)
            x, y = self.s16(t + 4) + MAP_OFFSET, self.s16(t + 6) + MAP_OFFSET
            if not (top <= y <= bottom and left <= x <= right) or self.r(t + 0x14, 2):
                continue
            if self.find(lid, group, num) is not None:
                continue
            slot = next((j for j in range(NOBJ) if not self.r(OBJ + j * OBJ_SIZE, 4) & 1), None)
            sid = next((j for j in range(1, NSPR) if not self.r(SPR + j * SPR_SIZE + 0x3E, 2) & 1), None)
            if slot is None or sid is None:
                continue
            o = OBJ + slot * OBJ_SIZE
            self.zero(o, OBJ_SIZE)
            self.w(o, 4, 0x1 | 0x4)
            self.w(o + 4, 1, sid)
            self.w(o + 5, 1, gfx)
            self.w(o + 6, 1, self.r(t + 9, 1))
            self.w(o + 8, 1, lid)
            self.w(o + 9, 1, num)
            self.w(o + 10, 1, group)
            self.w(o + 0x0B, 1, self.r(t + 8, 1) * 0x11)
            for off in (0x0C, 0x10, 0x14):
                self.ws16(o + off, x)
                self.ws16(o + off + 2, y)
            self.w(o + 0x18, 1, 0x11)
            self.w(o + 0x1C, 1, 0xFF)
            self._init_sprite(sid, slot, x, y)

    def find(self, lid, group, num):
        for j in range(NOBJ):
            o = OBJ + j * OBJ_SIZE
            if self.r(o, 4) & 1 and self.r(o + 8, 1) == lid and self.r(o + 9, 1) == num and self.r(o + 10, 1) == group:
                return j
        return None

    def remove_outside_view(self):
        left, right, top, bottom = self.view()
        for j in range(NOBJ):
            o = OBJ + j * OBJ_SIZE
            flags = self.r(o, 4)
            if not flags & 1 or flags & 0x10000:
                continue
            inside = any(left <= self.s16(o + off) <= right and top <= self.s16(o + off + 2) <= bottom
                         for off in (0x10, 0x0C))
            if not inside:
                sid = self.r(o + 4, 1)
                self.w(SPR + sid * SPR_SIZE + 0x3E, 2, 0)
                self.zero(o, OBJ_SIZE)
                self.anim.pop(j, None)

    def step(self):
        """One frame of held-movement processing for every object."""
        for j in range(NOBJ):
            o = OBJ + j * OBJ_SIZE
            flags = self.r(o, 4)
            if not flags & 1 or not flags & 0x40 or flags & 0x80:
                continue
            s = SPR + self.r(o + 4, 1) * SPR_SIZE
            if self.r(s + 0x32, 2) == 0:
                a = self.r(o + 0x1C, 1)
                self.actions.append((self.r(o + 8, 1), a))
                self.anim.pop(j, None)
                if a <= 0x03:
                    self.w(o + 0x18, 1, (a + 1) * 0x11)
                    self.w(o, 4, flags | 0x80)
                    continue
                if 0x10 <= a <= 0x13:
                    d, frames = a - 0x10 + 1, 16
                elif 0x1D <= a <= 0x20:
                    d, frames = a - 0x1D + 1, 8
                else:
                    self.w(o, 4, flags | 0x80)
                    continue
                dx, dy = DIRS[d]
                cx, cy = self.s16(o + 0x10), self.s16(o + 0x12)
                self.ws16(o + 0x14, cx)
                self.ws16(o + 0x16, cy)
                self.ws16(o + 0x10, cx + dx)
                self.ws16(o + 0x12, cy + dy)
                self.w(o + 0x18, 1, d * 0x11)
                self.w(s + 0x32, 2, 1)
                self.anim[j] = {"left": frames, "dx": dx, "dy": dy, "px": 16 // frames}
            st = self.anim.get(j)
            if st:
                self.ws16(s + 0x20, self.s16(s + 0x20) + st["dx"] * st["px"])
                self.ws16(s + 0x22, self.s16(s + 0x22) + st["dy"] * st["px"])
                st["left"] -= 1
                if st["left"] <= 0:
                    self.anim.pop(j)
                    self.w(s + 0x32, 2, 2)
                    self.w(o, 4, self.r(o, 4) | 0x80)

    # -- player control (what the person at the keyboard does) -------------
    def walk(self, d, action=None):
        dx, dy = DIRS[d]
        o = OBJ
        x, y = self.s16(SB1) + dx, self.s16(SB1 + 2) + dy
        self.ws16(SB1, x)
        self.ws16(SB1 + 2, y)
        self.ws16(o + 0x14, self.s16(o + 0x10))
        self.ws16(o + 0x16, self.s16(o + 0x12))
        self.ws16(o + 0x10, x + MAP_OFFSET)
        self.ws16(o + 0x12, y + MAP_OFFSET)
        self.w(o + 0x18, 1, d * 0x11)
        self.w(o + 0x1C, 1, action if action is not None else 0x10 + d - 1)
        # NPCs keep their map coords; on-screen sprite positions shift with the camera
        for j in range(1, NOBJ):
            ob = OBJ + j * OBJ_SIZE
            if self.r(ob, 4) & 1:
                s = SPR + self.r(ob + 4, 1) * SPR_SIZE
                self.ws16(s + 0x20, self.s16(s + 0x20) - dx * 16)
                self.ws16(s + 0x22, self.s16(s + 0x22) - dy * 16)
        self.camera_update()

    def face(self, d):
        self.w(OBJ + 0x18, 1, d * 0x11)
        self.w(OBJ + 0x1C, 1, d - 1)

    # -- inspection ----------------------------------------------------------
    def objects(self):
        out = []
        for j in range(1, NOBJ):
            o = OBJ + j * OBJ_SIZE
            flags = self.r(o, 4)
            if flags & 1:
                s = SPR + self.r(o + 4, 1) * SPR_SIZE
                out.append({
                    "slot": j, "localId": self.r(o + 8, 1), "gfx": self.r(o + 5, 1),
                    "x": self.s16(o + 0x10) - MAP_OFFSET, "y": self.s16(o + 0x12) - MAP_OFFSET,
                    "facing": self.r(o + 0x18, 1) & 0xF, "invisible": bool(flags & 0x2000),
                    "moving": bool(flags & 0x40) and not flags & 0x80,
                    "sx": self.s16(s + 0x20), "sy": self.s16(s + 0x22),
                })
        return out

    def puppets(self):
        return [o for o in self.objects() if 0x70 <= o["localId"] < 0x77]

    def player_sprite_xy(self):
        return self.s16(SPR + 0x20), self.s16(SPR + 0x22)
