import requests

body = {"coordinates":[[8.681495,49.41461],[8.686507,49.41943],[8.687872,49.420318]],"attributes":["avgspeed","percentage","detourfactor"],"continue_straight":"false","elevation":"false","extra_info":["steepness","surface","waytype"],"geometry_simplify":"false","id":12,"instructions":"true","instructions_format":"text","language":"es","maneuvers":"false","preference":"fastest","radiuses":100,"roundabout_exits":"false","skip_segments":2,"suppress_warnings":"false","units":"km","geometry":"true","maximum_speed":0.3}

headers = {
    'Accept': 'application/json, application/geo+json, application/gpx+xml, img/png; charset=utf-8',
    'Authorization': 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjVlYmZmNzk4OGE3YzQ3MmNiZDk5NGI1MGE2MWJjMDhjIiwiaCI6Im11cm11cjY0In0=',
    'Content-Type': 'application/json; charset=utf-8'
}
call = requests.post('https://api.openrouteservice.org/v2/directions/driving-car/geojson', json=body, headers=headers)

print(call.status_code, call.reason)
print(call.text)