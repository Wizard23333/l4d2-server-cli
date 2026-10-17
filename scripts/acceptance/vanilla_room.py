"""Destructive acceptance of ONLY the registered CLI main test room.

Run on a dedicated test installation that is already installed and stopped.
This changes its configuration and repeatedly starts/stops it. The final room
is public, uses coop c1m1_hotel, has no join password and has autostart disabled.
It does not install/download content, change firewalls, or touch other units.
Evidence and the synthetic RCON password stay in the private evidence directory.
"""
import argparse,json,os,pathlib,secrets,subprocess,sys,time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[2]/'bin/l4d2')
parser.add_argument('--expected-game-dir',required=True,help='Refuse a different registered target')
parser.add_argument('--evidence-dir',required=True,type=pathlib.Path,help='New private directory for results')
parser.add_argument('--allow-service-changes',action='store_true',help='Acknowledge main test room configuration and restarts')
args=parser.parse_args()
if os.name!='posix' or os.geteuid()!=0 or not args.allow_service_changes:
 parser.error('Requires Linux root and --allow-service-changes for the dedicated test room')
if not args.cli.is_file():parser.error('CLI entry does not exist')

base=args.evidence_dir.resolve()
base.mkdir(mode=0o700,parents=False,exist_ok=False)
cli=[sys.executable,str(args.cli.resolve())]
os.umask(0o077)
report={"version":subprocess.check_output([*cli,"--version"],text=True).strip(),"steps":[],"client_join":"not_verified","external_query":"not_verified"}
def save():
 (base/"lifecycle.json").write_text(json.dumps(report,indent=2))
def call(label,args,budget=220):
 started=time.time()
 p=subprocess.run([*cli,*args,"--json"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=budget)
 try:payload=json.loads(p.stdout)
 except ValueError:payload={"ok":False,"error":{"code":"invalid_cli_json"}}
 entry={"case":label,"exit_code":p.returncode,"seconds":round(time.time()-started,3),"result":payload}
 report["steps"].append(entry);save()
 print(json.dumps({"case":label,"exit_code":p.returncode,"ok":payload.get("ok"),"error":payload.get("error")}),flush=True)
 if p.returncode or not payload.get("ok"):
  (base/(label+".private.log")).write_text(p.stdout+"\n"+p.stderr)
  raise RuntimeError("CLI case failed: "+label)
 return payload["data"]
def status(label):
 return call(label,["server","status"])
def pid(data):return data["service"].get("MainPID")
try:
 initial=status("INT-02-installed-stopped")
 assert initial["desired_config"]["game_dir"]==args.expected_game_dir, "Registered target differs"
 assert initial["installation"]=="installed" and not pid(initial), "Must start from installed, stopped main test room"
 enabled=subprocess.run(["systemctl","is-enabled","l4d2-main.service"],stdout=subprocess.PIPE,text=True).stdout.strip()
 assert enabled=="disabled",enabled
 # Check the real privilege boundary without reading any secret values.
 role_checks={
  "game_can_execute_tool":subprocess.run(["runuser","-u","l4d2","--","test","-x","/var/lib/l4d2-cli/steamcmd/steamcmd.sh"]).returncode==0,
  "other_user_cannot_read_tool_cache":subprocess.run(["runuser","-u","nobody","--","test","-r","/var/lib/l4d2-cli/steamcmd/Steam/config/config.vdf"]).returncode!=0,
 }
 assert all(role_checks.values());report["role_checks"]=role_checks;save()
 join=base/"test-join.txt";rcon=base/"test-rcon.txt"
 for p in [join,rcon]:
  p.write_text(secrets.token_urlsafe(24));p.chmod(0o600)
 call("CFG-01-create-official-room",["server","configure","--visibility","public","--name","L4D2 CLI Vanilla Test","--bind","0.0.0.0","--port","27015","--map","c1m1_hotel","--mode","coop","--difficulty","normal","--no-autostart","--clear-join-password","--rcon-password-file",str(rcon),"--yes"])
 first=call("RUN-01-start",["server","start","--timeout","120"])
 assert first["game_ready"] and first["query"]["map"]=="c1m1_hotel" and not first["query"]["password_required"]
 one=status("STA-01-ready");first_pid=pid(one)
 again=call("RUN-02-repeat-start",["server","start","--timeout","120"])
 assert again["already_running"] and pid(again)==first_pid
 noop=call("INS-02-idempotent-install",["server","install","--yes","--timeout","120"])
 assert noop["already_installed"] and pid(status("INS-02-after-status"))==first_pid
 changed=call("CFG-08-running-save",["server","configure","--visibility","public","--name","L4D2 CLI Config Test","--map","c2m1_highway","--difficulty","hard","--yes"])
 pending=status("CFG-08-pending-restart")
 assert pid(pending)==first_pid and pending["query"]["map"]=="c1m1_hotel" and pending["configuration_pending"] is True
 restarted=call("RUN-06-restart",["server","restart","--yes","--timeout","180"])
 assert restarted["game_ready"] and restarted["query"]["map"]=="c2m1_highway"
 assert restarted["service"]["InvocationID"]!=one["service"]["InvocationID"]
 stopped=call("RUN-05-stop",["server","stop","--yes","--timeout","60"])
 assert stopped["game_port_released"] and stopped["service_processes_remaining"]==[]
 stopped_again=call("RUN-05-repeat-stop",["server","stop","--yes","--timeout","60"])
 assert stopped_again["already_stopped"]
 call("RUN-01-start-after-stop",["server","start","--timeout","120"])
 call("CFG-final-public-official-room",["server","configure","--visibility","public","--name","L4D2 CLI Vanilla","--map","c1m1_hotel","--mode","coop","--difficulty","normal","--clear-join-password","--no-autostart","--yes"])
 final=call("RUN-final-restart",["server","restart","--yes","--timeout","180"])
 assert final["query"]["map"]=="c1m1_hotel" and final["query"]["password_required"] is False
 final_status=status("STA-final-ready")
 assert final_status["game_ready"] and final_status["configuration_pending"] is False
 report["final_ready"]=True;report["server_created"]=True;save()
 call("DIA-01-doctor",["doctor"])
 print("VANILLA_ROOM_READY",json.dumps({"map":final["query"]["map"],"port":27015,"join_password_required":False,"client_join":"not_verified"}),flush=True)
except BaseException as e:
 report["failure"]=type(e).__name__+": "+str(e);save()
 raise
