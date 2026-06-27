import httpx, json, asyncio

async def check():
    url = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"
    tests = [
        {"conditions": {"group_id":"0041","status":1,"shipment_no":"11495287"}, "label": "string+status:1"},
        {"conditions": {"group_id":"0041","shipment_no":"11495287"}, "label": "no-status"},
        {"conditions": {"group_id":"0041","status":1,"shipment_no":11495287}, "label": "int+status:1"},
        {"conditions": {"shipment_no":"11495287"}, "label": "no group_id"},
    ]
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for t in tests:
            p = {
                "conditions": json.dumps(t["conditions"]),
                "fields":     json.dumps({"shipment_no":1,"vehicle_no":1,"trip_status":1,"status":1,"run_date":1}),
                "table":      "courier_trip_detail"
            }
            r   = await client.post(url, data=p, headers={"Content-Type":"application/x-www-form-urlencoded"})
            d   = r.json()
            cnt = len(d) if isinstance(d, list) else d
            print(f"[{t['label']}]: {cnt} records")
            if isinstance(d, list) and d:
                print(f"  status={d[0].get('status')} trip_status={d[0].get('trip_status')} run_date={str(d[0].get('run_date',''))[:10]}")

asyncio.run(check())