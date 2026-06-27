import httpx, json, asyncio

MONGO_API = "http://3.110.228.31/secutrak_local_mongo/access/v0.1/selectQuery"

async def check():
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)) as client:

        # Test 1: _id as string
        print("Test 1: m_trip_id as plain string...")
        p = {
            "conditions": json.dumps({"group_id":"0041","status":1,"m_trip_id":"6a064b95b38dadc0150fb2e9"}),
            "fields":     json.dumps({"location_name":1,"pod_status":1,"location_sequence":1}),
            "table":      "courier_trip_detail_customer",
            "limit":      "3"
        }
        r = await client.post(MONGO_API, data=p, headers={"Content-Type":"application/x-www-form-urlencoded"})
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.text[:200]}")

        print()

        # Test 2: What fields does courier_trip_detail_customer have?
        print("Test 2: Sample record from courier_trip_detail_customer...")
        p2 = {
            "conditions": json.dumps({"group_id":"0041","status":1}),
            "fields":     json.dumps({}),
            "table":      "courier_trip_detail_customer",
            "limit":      "1"
        }
        r2 = await client.post(MONGO_API, data=p2, headers={"Content-Type":"application/x-www-form-urlencoded"})
        print(f"  Status: {r2.status_code}")
        if r2.status_code == 200:
            d2 = r2.json()
            if isinstance(d2, list) and d2:
                print(f"  Keys: {list(d2[0].keys())}")
                print(f"  m_trip_id value: {d2[0].get('m_trip_id')} (type: {type(d2[0].get('m_trip_id')).__name__})")
                print(f"  Sample: {d2[0]}")

asyncio.run(check())