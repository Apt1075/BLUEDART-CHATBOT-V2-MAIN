import httpx, json, asyncio

MONGO_API = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"

async def check():
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)) as client:

        # Test: vehicle_no_prm sorted by run_date_prm desc — latest trip stops
        print("Test: vehicle_no_prm HR55AJ9358 sorted latest first...")
        p = {
            "conditions": json.dumps({"group_id":"0041","status":1,"vehicle_no_prm":"HR55AJ9358"}),
            "fields":     json.dumps({"location_name":1,"pod_status":1,"location_sequence":1,
                                       "run_date_prm":1,"schedule_time_arrival":1,
                                       "poa_status":1,"sequence_no":1}),
            "table":      "courier_trip_detail_customer",
            "sort":       "run_date_prm:desc",
            "limit":      "20"
        }
        r = await client.post(MONGO_API, data=p, headers={"Content-Type":"application/x-www-form-urlencoded"})
        d = r.json()
        cnt = len(d) if isinstance(d, list) else 0
        print(f"  Records: {cnt}")
        if isinstance(d, list) and d:
            # Group by run_date_prm to find latest trip
            dates = {}
            for rec in d:
                rd = str(rec.get("run_date_prm",""))[:10]
                dates[rd] = dates.get(rd, 0) + 1
            print(f"  Dates found: {dict(sorted(dates.items(), reverse=True))}")
            print(f"  Latest record run_date_prm: {d[0].get('run_date_prm')}")
            print(f"  Latest stops:")
            latest_date = str(d[0].get("run_date_prm",""))[:10]
            latest_stops = [r for r in d if str(r.get("run_date_prm","")).startswith(latest_date)]
            for s in sorted(latest_stops, key=lambda x: x.get("location_sequence",0)):
                print(f"    seq={s.get('location_sequence')} | {s.get('location_name')} | pod={s.get('pod_status')}")

asyncio.run(check())