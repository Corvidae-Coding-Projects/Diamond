#!/usr/bin/env python3
"""Real CLI/native LP4 replay; independent requirements supplied each expectation."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess,tempfile,shutil
ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument("--binary",type=Path,default=ROOT.parents[2]/"target/debug/diamond");parser.add_argument("--suffix",default="replay");args=parser.parse_args();WORK=Path(tempfile.mkdtemp(prefix="diamond-lp4-independent-"))
# check/build, expected acceptance. Build failures must occur before artifact publication.
CASES=[("positive","build",True),("empty_status","build",True),("mixed_scalar_controls","build",True),("missing_label","check",False),("blank_label","check",False),("submit_outside_schema","check",False),("missing_alt","check",False),("raw_id_collision","build",False),("large_choice_forms","build",False),("large_status_list","build",False),("click_nominal","check",False),("output_nominal","check",False),("nested_secret_output","check",False),("child_identity","check",False),("recursive_components","check",False),("root_identity","build",True),("dynamic_reserved_id","build",False)]
report={"binary_sha256":hashlib.sha256(args.binary.read_bytes()).hexdigest(),"observations":[]}
for name,mode,accepted in CASES:
 command=[str(args.binary.resolve()),mode,str(ROOT/name/"diamond.toml")];output=WORK/name
 if mode=="build":command += ["--out",str(output)]
 result=subprocess.run(command,capture_output=True,text=True)
 markers={"missing_label":"accessible label/value is required","blank_label":"accessible names require nonempty Text","submit_outside_schema":"submit belongs inside","missing_alt":"images require an author-supplied alt","raw_id_collision":"reserved by the runtime","large_choice_forms":"render node capacity","large_status_list":"render node capacity","click_nominal":"argument type mismatch","output_nominal":"output handler signature mismatch","nested_secret_output":"recursively safe managed data","child_identity":"direct root-component assignment","recursive_components":"component cycle","dynamic_reserved_id":"reserved by the runtime"}
 observation={"case":name,"command":command,"expected_accepted":accepted,"exit":result.returncode,"pass":(result.returncode==0)==accepted,"stderr":result.stderr}
 if not accepted:observation["expected_diagnostic"]=markers[name];observation["pass"] &= markers[name] in result.stderr
 if mode=="build" and not accepted:observation["artifact_absent"]=not output.exists();observation["pass"] &= not output.exists()
 if name=="mixed_scalar_controls" and result.returncode==0:
  html=(output/"index.html").read_text();ids=re.findall(r' id="([^"]+)"',html);labels=re.findall(r'<label for="([^"]+)"',html)
  observation["markup"]={"ids":len(ids),"labels":len(labels),"unique_ids":len(ids)==len(set(ids)),"labels_resolve_once":all(ids.count(i)==1 for i in labels),"selects":html.count("<select"),"checkboxes":html.count('type="checkbox"'),"has_unicode":"雪😀" in html}
  observation["pass"] &= observation["markup"]["unique_ids"] and observation["markup"]["labels_resolve_once"] and len(labels)==12 and observation["markup"]["selects"]==4 and observation["markup"]["checkboxes"]==2 and observation["markup"]["has_unicode"]
 report["observations"].append(observation)
report["pass"]=all(o["pass"] for o in report["observations"]);(ROOT/("results-"+args.suffix+".json")).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n");print(json.dumps({"pass":report["pass"],"cases":len(report["observations"])}));shutil.rmtree(WORK);raise SystemExit(not report["pass"])
