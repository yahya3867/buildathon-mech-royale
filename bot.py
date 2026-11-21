import heapq
import json
import math
from pathlib import Path

import mechfight as mf

CELL = 6
DIRECTIONS = [(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1) if x or y]

def cell(x, y):
    return (math.floor(x / CELL), math.floor(y / CELL))

def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

def inside(x, y, ring):
    odd = False
    px, py = ring[-2:]
    for nx, ny in zip(ring[::2], ring[1::2]):
        if (ny > y) != (py > y) and x < nx + (px - nx) * (y - ny) / (py - ny):
            odd = not odd
        px, py = nx, ny
    return odd

class Bot:
    name = "Switchback"

    def __init__(self, hello):
        self.reach = hello["config"]["loot"]["pickup_radius"]
        self.blocked = set()
        self.route = []
        self.last_plan = -200
        self.destination = None
        directory = Path(hello["map"]["dir"])
        for path in (directory / "v2/world.json", directory / "world.json"):
            if path.is_file():
                world = json.loads(path.read_bytes())
                for prism in world["prisms"]:
                    ring = prism["ring"]
                    if prism["kind"] == "bridge":
                        continue
                    for i in range(min(ring[::2]).__floor__() // CELL - 1, math.ceil(max(ring[::2]) / CELL) + 1):
                        for j in range(math.floor(min(ring[1::2]) / CELL) - 1, math.ceil(max(ring[1::2]) / CELL) + 1):
                            if any(inside(i*CELL+dx, j*CELL+dy, ring) for dx,dy in ((3,3),(1,1),(1,5),(5,1),(5,5))):
                                self.blocked.add((i,j))
                break
        self.side = 1 if hello["slot"] % 2 else -1

    def route_to(self, start, end):
        queue = [(distance(start, end), 0, start)]
        cost, previous = {start: 0}, {}
        nearest = start
        for _ in range(850):
            if not queue:
                break
            _, spent, at = heapq.heappop(queue)
            if spent != cost[at]:
                continue
            if distance(at, end) < distance(nearest, end):
                nearest = at
            if nearest == end:
                break
            for dx, dy in DIRECTIONS:
                nxt = (at[0]+dx, at[1]+dy)
                if nxt in self.blocked or (dx and dy and ((at[0]+dx,at[1]) in self.blocked or (at[0],at[1]+dy) in self.blocked)):
                    continue
                step = spent + (1.415 if dx and dy else 1)
                if step < cost.get(nxt, float("inf")):
                    cost[nxt], previous[nxt] = step, at
                    heapq.heappush(queue, (step + 1.1*distance(nxt,end), step,nxt))
        route = []
        while nearest != start:
            route.append(((nearest[0]+0.5)*CELL,(nearest[1]+0.5)*CELL))
            nearest = previous[nearest]
        return route

    def act(self, obs):
        me, zone = obs["self"], obs["zone"]
        x,y,z = me["pos"]
        pos = (x,y)
        def nearest(items):
            return min(items, key=lambda p: distance(pos,p["pos"]), default=None)
        enemy = nearest([m for m in obs["mechs"] if m["kind"] == "mech"])
        target = enemy or nearest(obs["crates"]) or nearest(obs["mechs"])
        loot = nearest([p for p in obs["pickups"] if p["kind"] in ("laser","lightning")])
        cube = nearest(obs["cubes"])
        center = zone["next_center"]
        radius = max(4,zone["next_radius"] * 0.64)
        phase = obs["tick"] * 0.003 * self.side + me["id"]
        goal = (center[0]+radius*math.cos(phase),center[1]+radius*math.sin(phase))
        danger = enemy and distance(pos,enemy["pos"]) < 115
        if danger:
            dx,dy = x-enemy["pos"][0],y-enemy["pos"][1]
            mag=max(1,math.hypot(dx,dy))
            goal=(x+(dx*0.4-dy*self.side)*48/mag,y+(dy*0.4+dx*self.side)*48/mag)
        elif cube and distance(pos,cube["pos"]) < 75:
            goal=cube["pos"]
        elif loot and not me["weapons"][1] and distance(pos,loot["pos"]) < 90:
            goal=loot["pos"]
        elif obs.get("drops"):
            drop=nearest(obs["drops"])
            if distance(pos,drop["pos"])<100:
                goal=drop["pos"]
        if distance(pos,zone["center"]) > zone["radius"]-12:
            goal=center
        dest=cell(*goal[:2])
        if obs["tick"]-self.last_plan>=12 and (not self.route or dest!=self.destination):
            self.route=self.route_to(cell(x,y),dest)
            self.last_plan,self.destination=obs["tick"],dest
        while self.route and distance(pos,self.route[-1]) < 4:
            self.route.pop()
        waypoint=self.route[-1] if self.route else goal
        yaw=math.atan2(waypoint[1]-y,waypoint[0]-x)
        act={"move":[math.cos(yaw),math.sin(yaw)],"aim":[yaw,0],"boost":bool(danger or not me["in_zone"]),"jump":obs["tick"]%30==0}
        if loot and distance(pos,loot["pos"]) < self.reach and not me["weapons"][1]:
            act["take"]=loot["id"]
        if obs["offer"]:
            priorities=["armor","regen","speed","gun_damage","gun_rate"]
            act["pick"]=min(range(len(obs["offer"]["choices"])),key=lambda i: priorities.index(k) if (k:=obs["offer"]["choices"][i]["id"]) in priorities else 99)
        if target:
            tx,ty,tz=target["pos"]
            d=distance(pos,(tx,ty))
            secondary=me["weapons"][1]
            laser=secondary and secondary["kind"]=="laser"
            speed=(me["weapons"][0] or {}).get("stats",{}).get("speed",200)
            lead=0 if laser else min(0.9,d/max(1,speed))
            vx,vy,vz=target.get("vel",[0,0,0])
            dx,dy=tx+vx*lead-x,ty+vy*lead-y
            aim=math.atan2(dy,dx)
            act["aim"]=[aim, math.atan2(tz+vz*lead+(4.5 if "kind" in target else 1.4)-z-me["stats"].get("muzzle_height",7),max(1,math.hypot(dx,dy)))]
            aligned=abs(math.remainder(aim-me["aim"][0],2*math.pi)) < max(0.07,3/max(1,d))
            act["fire"]=[aligned,bool(secondary and (secondary.get("charge",0)<0.73 if laser else aligned and d<45))]
        else:
            act["reload"]=True
        return act

mf.run(Bot)
