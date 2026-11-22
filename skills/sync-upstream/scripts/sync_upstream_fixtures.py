from collections import namedtuple

ForkPair = namedtuple("ForkPair", "origin_seed base origin upstream upstream_seed ahead")
GH = '''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["FAKE_REPO"]))
args = sys.argv[1:]
if args[:2] == ["api", "repos/" + state["name"]]:
    print(json.dumps(state["info"]))
elif args[:2] == ["repo", "view"]:
    print(state.get("view", state["name"]))
elif args[:2] == ["pr", "view"]:
    field = args[args.index("--json") + 1] if "--json" in args else "body"
    if field == "headRefOid":
        print(state["head"])
    elif field == "baseRefName":
        print(state.get("base", "main"))
    else:
        print(state["body"])
elif args[0] == "api":
    endpoint = next(x for x in args if x.startswith("repos/"))
    key = endpoint.split("?", 1)[0]
    if key.startswith("repos/" + state["name"] + "/rules/branches/"):
        print(json.dumps([state.get("applied_rules", [])]))
    elif key.endswith("/check-runs"):
        head = key.split("/commits/")[1].removesuffix("/check-runs")
        print(json.dumps([{"check_runs": state.get("check_runs", {}).get(head, [])}]))
    elif key.endswith("/status"):
        print(json.dumps({"state": "success", "statuses": state.get("statuses", [])}))
    elif key.endswith("/statuses"):
        print(json.dumps(state.get("status_pages", [state.get("statuses", [])])))
    else:
        print("unexpected gh api call: " + " ".join(args), file=sys.stderr)
        sys.exit(1)
else:
    print("unexpected gh call: " + " ".join(args), file=sys.stderr)
    sys.exit(1)
'''
