import argparse
import json
import os
import pathlib
import sys
import threading
from http.server import HTTPServer
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import checkwarranty_glpi_sync as sync
from mock_api import Handler, REQUESTS
major=sys.argv[1]
auth=json.loads(pathlib.Path("/tmp/cw-glpi-cli-auth-"+major+".json").read_text())
server=HTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
os.environ.update(GLPI_URL="http://127.0.0.1:180"+major,GLPI_APP_TOKEN=auth["app_token"],GLPI_USER_TOKEN=auth["user_token"],CHECKWARRANTY_API_KEY="acceptance-sandbox-only",CHECKWARRANTY_API_URL="http://127.0.0.1:"+str(server.server_port)+"/api/v1")
args=argparse.Namespace(dry_run=False,plan=True,limit=100,force=True,recheck_days=30,brand=None,sync_specs=True,verbose=False)
sync.run_sync(args)
assert not REQUESTS
args.plan=False
args.state_file = "/tmp/cw-glpi-cursor-"+major+".json"
args.limit = 2
with patch.object(sync.time,"sleep"):
    for _ in range(3):
        sync.run_sync(args)
client=sync.GlpiClient(os.environ["GLPI_URL"],auth["app_token"],auth["user_token"])
assert client.init_session()
for kind,id_ in auth["ids"].items():
    row=client.get_computer_infocom(id_)
    assert row["buy_date"]=="2020-03-01"
    if kind in ("ENDONLY","FULL","ASYNC"):
        assert "expiry="+("2027-01-01" if kind=="FULL" else "2027-01-05") in row["warranty_info"]
    if kind=="ENDONLY": assert not row["warranty_date"]
    if kind=="FULL": assert row["warranty_date"]=="2024-01-01" and int(row["warranty_duration"])==36
    if kind=="SPECS": assert "Xeon" in row["warranty_info"]
    if kind=="NOTFOUND": assert row["warranty_info"]=="Manual note"
client.kill_session()
assert len(REQUESTS)==5
sync.write_cursor(args, os.environ['GLPI_URL'], 999999)
sync.run_sync(args)
assert sync.read_cursor(args, os.environ['GLPI_URL']) == 0
assert len(REQUESTS)==5
server.shutdown()
print("GLPI "+major+" REST CLI acceptance passed")
