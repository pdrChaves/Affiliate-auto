"""Gerador de carga assíncrono: RPS e latência p50/p95/p99."""
import asyncio, json, sys, time, statistics, httpx
A = ("admin", "troque-esta-senha")

async def run(base, path, conc, secs, method="GET", data=None):
    lat, errs, codes = [], 0, {}
    end = time.perf_counter() + secs
    async with httpx.AsyncClient(base_url=base, auth=A, timeout=30,
                                 limits=httpx.Limits(max_connections=conc)) as c:
        async def worker():
            nonlocal errs
            while time.perf_counter() < end:
                t = time.perf_counter()
                try:
                    r = await c.request(method, path, data=data)
                    codes[r.status_code] = codes.get(r.status_code, 0) + 1
                    if r.status_code >= 500: errs += 1
                except Exception:
                    errs += 1
                lat.append((time.perf_counter() - t) * 1000)
        t0 = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(conc)])
        el = time.perf_counter() - t0
    q = statistics.quantiles(lat, n=100) if len(lat) > 2 else [0]*99
    return {"rota": f"{method} {path}", "concorrencia": conc, "req": len(lat), "rps": round(len(lat)/el, 1),
            "p50_ms": round(q[49], 1), "p95_ms": round(q[94], 1), "p99_ms": round(q[98], 1),
            "max_ms": round(max(lat), 1), "erros": errs, "codes": codes}

if __name__ == "__main__":
    base, out = sys.argv[1], sys.argv[2]
    scen = json.loads(sys.argv[3])
    res = []
    for s in scen:
        r = asyncio.run(run(base, s["path"], s["c"], s.get("secs", 8), s.get("m", "GET"), s.get("data")))
        r["cenario"] = s.get("nome", "")
        print(json.dumps(r, ensure_ascii=False)); res.append(r)
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
