import hashlib

import pytest

from hermes_cli.theseus_research.library import connection, execute


@pytest.fixture(autouse=True)
def local_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def test_capture_and_reopen_exact_editions():
    args = dict(project="Purchasing", action="capture", kind="file", title="Quote", origin="/selected/quote.csv")
    original = execute(dict(args, text="Alder,USD 11\n"))
    assert execute(dict(args, text="Alder,USD 11\n")) == original
    later = execute(dict(args, text="Alder,USD 15\n"))
    assert later["source"] == original["source"] and later["edition"] != original["edition"]
    old = execute(dict(project="Purchasing", action="read", source=original["source"], edition=original["edition"]))
    assert old["text"] == "Alder,USD 11\n" and not old["current"]
    assert old["digest"] == hashlib.sha256(old["text"].encode()).hexdigest()
    assert execute(dict(project="Purchasing", action="search", query="Alder"))["sources"][0]["edition"] == later["edition"]
    assert execute(dict(project="Personal", action="search", query="Alder"))["sources"] == []
    with pytest.raises(ValueError):
        execute(dict(project="Personal", action="read", source=later["source"]))


def test_bookmark_and_note_are_different():
    args = dict(project="Study", action="capture", title="Study")
    saved = execute(dict(args, kind="web", origin="https://example.org/article"))
    assert execute(dict(project="Study", action="read", source=saved["source"]))["text"] == ""
    with pytest.raises(ValueError):
        execute(dict(args, text="   "))
    with pytest.raises(ValueError):
        execute(dict(args, kind="web", origin="file:///tmp/test"))


def test_crawl_pause_resume_budget_and_project_isolation(monkeypatch):
    from hermes_cli.theseus_research import crawl
    seen = []
    def fetch(url, host, deadline, rules=None):
        seen.append(url)
        if url.endswith("/robots.txt"):
            return 200,url,"text/plain","User-agent: *\nDisallow: /private"
        return 200,url,"text/html",'<title>Catalogue</title><p>A public listing</p><a href="/second">Next</a><a href="/private">Private</a><a href="https://example.net/other">Elsewhere</a>'
    monkeypatch.setattr(crawl,"fetch",fetch)
    def call(action,**args):
        return execute(dict(project="Books",action="crawl_"+action,**args))
    run = call("create",url="https://example.org/",question="public listing",pages=2,depth=1,seconds=30)["id"]
    assert call("step",crawl=run)["state"] == "paused" and seen==[]
    call("resume",crawl=run)
    first=call("step",crawl=run)
    assert [p["state"] for p in first["queue"]] == ["saved","queued"]
    call("pause",crawl=run)
    call("step",crawl=run)
    assert len(seen)==2
    with pytest.raises(ValueError):
        execute(dict(project="Elsewhere",action="crawl_status",crawl=run))
    call("resume",crawl=run)
    second=call("step",crawl=run)
    assert [p["state"] for p in second["queue"]] == ["saved","duplicate"]
    assert call("step",crawl=run)["state"]=="complete" and len(seen)==3


def test_cited_brief_edit_history_and_export():
    import csv
    from pathlib import Path
    def call(action, **args):
        return execute(dict(project="Options",action=action,**args))
    source=call("capture",title="Quote",text="9 days",kind="note")
    cell={"value":"9 days","edition":source["edition"],"quote":"9 days","note":"Keep separate"}
    brief=call("brief_create",title="Vendor",question="When?",rows=[{"name":"=Unsafe","cells":{"Terms":cell}}])
    bid=brief["brief"]
    with pytest.raises(ValueError):
        call("brief_edit",brief=bid,expected_revision=1,rows=[{"name":"=Unsafe","cells":{"Terms":dict(cell,quote="8 days")}}])
    later=call("brief_edit",brief=bid,expected_revision=1,rename_column="Terms",new_name="Delivery")
    assert later["rows"][0]["cells"]["Delivery"]["note"]=="Keep separate"
    with pytest.raises(ValueError):
        call("brief_edit",brief=bid,expected_revision=1,add_column="Notes")
    assert call("brief_read",brief=bid,revision=1)["columns"]==brief["columns"]
    with pytest.raises(ValueError):
        execute(dict(project="Different",action="brief_read",brief=bid))
    path=call("brief_export",brief=bid)["path"]
    with Path(path).open() as f:
        rows=list(csv.DictReader(f))
    assert rows[0]["Delivery"]=="9 days" and rows[0]["Delivery evidence"].endswith("9 days")
    assert rows[0]["Price"]=="Not recorded" and rows[0]["Item"]=="'=Unsafe"


def test_citation_beyond_first_page_and_preserved_annotation():
    src=execute(dict(project="Late",action="capture",title="Long source",text="X"*15000+" term 82 days.",kind="note"))
    args=dict(project="Late",title="Review",question="When?")
    cell={"value":"82 days","edition":src["edition"],"quote":"82 days","note":"Check holidays"}
    row={"name":"Order","cells":{"Terms":cell}}
    doc=execute(dict(args,action="brief_create",rows=[row]))
    loc=dict(project="Late",brief=doc["brief"])
    passage=execute(dict(loc,action="brief_passage",column="Terms",row="Order"))
    assert passage["offset"]>12000 and "82 days" in passage["text"]
    del cell["note"]
    changed=execute(dict(loc,action="brief_edit",expected_revision=1,rows=[row]))
    assert changed["rows"][0]["cells"]["Terms"]["note"]=="Check holidays"


def test_refresh_updates_unique_fields_and_preserves_notes_and_history():
    origin="/selected/prices.csv"
    def call(action,**args):
        return execute(dict(project="Refresh",action=action,**args))
    def capture(body):
        return call("capture",title="Prices",kind="file",origin=origin,text=body)
    old=capture("Vendor,Price,Terms\nA,8 USD,30 days\nB,5 USD,40 days\n")
    cell={"value":"8 USD","note":"Hold until Friday","quote":"A,8 USD,30 days","edition":old["edition"]}
    doc=call("brief_create",title="Decision",question="Which?",rows=[{"name":"A","cells":{"Price":cell,"Terms":dict(cell,value="Net thirty")}}])
    latest=capture("Vendor,Price,Terms\nA,9 USD,30 days\nB,5 USD,40 days\n")
    args=dict(brief=doc["brief"],expected_revision=1,row="A",new_edition=latest["edition"])
    pending=call("refresh_compare",brief=doc["brief"])["changes"]
    assert [(x["column"],x["state"]) for x in pending]==[("Price","changed"),("Terms","review")]
    with pytest.raises(ValueError):call("refresh_apply",column="Terms",**args)
    updated=call("refresh_apply",column="Price",**args)
    saved=updated["rows"][0]["cells"]["Price"]
    assert (saved["value"],saved["note"],saved["edition"])==("9 USD","Hold until Friday",latest["edition"])
    assert call("brief_read",brief=doc["brief"],revision=1)["rows"][0]["cells"]["Price"]==doc["rows"][0]["cells"]["Price"]
    capture("Vendor,Price,Terms\nA,10 USD,30 days\nA,11 USD,30 days\n")
    assert all(x["state"]=="review" for x in call("refresh_compare",brief=doc["brief"])["changes"])
    with pytest.raises(ValueError):call("refresh_apply",column="Price",**args)


def test_one_batch_scenarios_and_cash_timing():
    inputs=dict(action="scenario_save",project="Co-op",title="Batch",currency="USD",price="90",cost="68",fixed=200,quantity=20,
                lead_days=14,supplier_days=10,customer_days=7)
    first=execute(inputs)["scenarios"][0]
    assert first["result"]==dict(contribution="22.00",profit="240.00",break_even_units=10,price_floor="78.00",cash_before_collection="1560.00",first_receipt_day=21)
    assert len(execute(inputs)["scenarios"])==1
    next_case=execute(dict(inputs,quantity=5,supplier_days=30))["scenarios"][0]["result"]
    assert next_case["profit"]=="-90.00" and next_case["cash_before_collection"]=="200.00"
    assert execute(dict(inputs,price=67))["scenarios"][0]["result"]["break_even_units"] is None
    for key,val in [("quantity","1.25"),("cost","nan"),("fixed",-1),("quantity",0)]:
        with pytest.raises(ValueError):execute(dict(inputs,**{key:val}))
    assert execute(dict(project="Other",action="scenario_list"))["scenarios"]==[]


def test_measurement_analysis_pins_rows_and_flags_exclusions():
    base=dict(project="Measuring")
    source=execute(dict(base,action="capture",title="Measurements",kind="file",origin="/selected/measure.tsv",text="group\tmm\nred\t2\nred\t8\nred\tNA\nblue\t4\nred\t5\textra"))
    args=dict(base,action="analysis_run",source=source["source"],title="Dimensions",measure="mm",group="group",unit="mm")
    old=execute(args)["runs"][0]
    assert old["report"]["excluded_count"]==2
    red=next(g for g in old["report"]["groups"] if g["group"]=="red")
    assert (red["n"],red["mean"],red["max_record"])==(2,"5",3)
    execute(dict(base,action="capture",title="Measurements",kind="file",origin="/selected/measure.tsv",text="group\tmm\nred\t20"))
    new=execute(args)["runs"][0]
    assert new["edition"]!=old["edition"] and new["report"]["groups"][0]["mean"] in ("2E+1","20")
    assert execute(dict(base,action="analysis_record",run=old["id"],record=3))["values"]==["red","8"]
    with pytest.raises(ValueError):execute(dict(action="analysis_record",project="Other",run=old["id"],record=3))
    assert len(execute(args)["runs"])==2


def test_workflow_reuse_keeps_procedure_without_facts_or_assumed_success():
    def call(project,action,**kw):return execute(dict(project=project,action=action,**kw))
    source=call("Vendor","brief_create",title="Supplier Comparison",question="Who?",rows=[{"name":"Secret supplier","cells":{"Price":{"value":"99"}}}])
    wf=call("Vendor","workflow_save",brief=source["brief"],title="Compare bids",steps="Check prices",output="An editable table",review="Confirm terms")["workflows"][0]
    job=call("Trip","workflow_start",workflow=wf["id"],title="Air fare",question="Best fit?")["jobs"][0]
    empty=call("Trip","brief_read",brief=job["brief"]);assert not empty["rows"] and empty["columns"]==source["columns"]
    call("Trip","workflow_advance",job=job["id"],expected_step=0,note="Requested fares")
    with pytest.raises(ValueError):call("Trip","workflow_advance",job=job["id"],expected_step=0,note="Retry")
    with pytest.raises(ValueError):call("Trip","workflow_advance",job=job["id"],expected_step=1,note="Premature review")
    call("Trip","brief_edit",brief=job["brief"],expected_revision=1,rows=[{"name":"Train","cells":{"Price":{"value":"26"}}}])
    done=call("Trip","workflow_advance",job=job["id"],expected_step=1,note="Reviewed fare against ticket")["jobs"][0]
    assert done["progress"][-1]["brief_revision"]==2 and len(done["progress"])==2


def test_timeboxed_attention_and_source_pinning():
    def call(p,a,**args):return execute(dict(project=p,action=a,**args))
    src=call("Trip","capture",kind="file",title="Times",origin="/selected/times.csv",text="depart,08:24")
    it=call("Trip","attention_capture",title="Review booking",state="Needs decision",source=src["source"],minutes=15)["items"][0]
    call("Trip","capture",kind="file",title="Times",origin="/selected/times.csv",text="depart,08:47")
    call("Purchasing","attention_capture",title="Wait for vendor",state="Waiting",minutes=10)
    call("Purchasing","attention_capture",title="Price shipment",state="Ready",minutes=45)
    assert [r["title"] for r in call("Inbox","attention_list",all_projects=True,max_minutes=20,filter="Needs decision")["items"]]==["Review booking"]
    assert call("Trip","read",source=it["source"],edition=it["edition"])["text"]=="depart,08:24"
    call("Trip","attention_change",item=it["id"],revision=1,state="Done")
    with pytest.raises(ValueError):call("Trip","attention_change",item=it["id"],revision=1,state="Ready")
    assert call("Trip","attention_list")["items"]==[]


def test_explicit_cross_project_pin_only_grants_the_selected_edition():
    def call(p,a,**args):return execute(dict(project=p,action=a,**args))
    base=dict(kind="file",title="Pricing",origin="/selected/pricing.csv")
    old=call("Sourcing","capture",**base,text="Quartz costs 68 USD")
    assert not call("Garden","search",query="Quartz")["sources"]
    hit=call("Garden","discovery_search",query="Quartz")["matches"][0];assert hit["origin_project"]=="Sourcing"
    call("Garden","discovery_pin",source=old["source"],edition=old["edition"],origin_project="Sourcing")
    latest=call("Sourcing","capture",**base,text="Now costs 77 USD")
    assert call("Garden","search",query="Quartz")["sources"][0]["edition"]==old["edition"]
    assert call("Garden","read",source=old["source"])["text"]=="Quartz costs 68 USD"
    with pytest.raises(ValueError):call("Garden","read",source=old["source"],edition=latest["edition"])
    doc=call("Garden","brief_create",title="Cost",question="What price?",rows=[{"name":"Quartz","cells":{"Price":{"value":"68 USD","edition":old["edition"],"quote":"68 USD"}}}])
    assert "68 USD" in call("Garden","brief_passage",brief=doc["brief"],row="Quartz",column="Price")["text"]
    assert call("Other","discovery_list")["pins"]==[]


def test_export_reuse_is_only_a_reversible_scoped_suggestion():
    def call(a,**kw):return execute(dict(project="Plan",action=a,**kw))
    args=dict(question="Compare",columns=["Cost","Access"],rows=[{"name":"Option","cells":{"Access":{"value":"Open"}}}])
    a=call("brief_create",title="Initial",**args)
    for _ in range(3):call("brief_export",brief=a["brief"])
    empty=call("brief_create",title="Blank",question="Why?",columns=args["columns"])
    call("brief_export",brief=empty["brief"])
    assert call("preference_list")["suggestions"]==[]
    b=call("brief_create",title="Another",**args);call("brief_export",brief=b["brief"])
    sug=call("preference_list")["suggestions"][0];assert len(sug["receipts"])==2 and not sug["active"]
    key=dict(template=sug["template"],fingerprint=sug["fingerprint"])
    call("preference_accept",**key)
    assert call("brief_create",title="Uses default",question="Which?")["columns"]==args["columns"]
    other=execute(dict(project="Unrelated",action="brief_create",title="Normal",question="Which?"))
    assert other["columns"]!=args["columns"]
    call("preference_undo",**key)
    assert call("brief_create",title="Back to built-in",question="Which?")["columns"]==other["columns"]


def test_review_layers_and_actual_export_are_independent():
    from pathlib import Path
    def call(action,**args):return execute(dict(project="Checks",action=action,**args))
    source=call("capture",title="Measured width",text="Surface width 2 mm.")
    c=dict(value="The sample is strong",edition=source["edition"],quote="Surface width 2 mm.")
    doc=call("brief_create",title="Interpretation",question="Strength?",rows=[{"name":"Sample","cells":{"Terms":c}}])
    loc=dict(brief=doc["brief"],row="Sample",column="Terms")
    path=Path(call("brief_export",brief=doc["brief"])["path"])
    v=call("evidence_history",**loc)["versions"][0]
    assert v["evidence"]["located"] and not v["reviews"] and v["export"]["status"]=="Verified export"
    v=call("evidence_review",**loc,revision=1,layer="Conclusion",verdict="Insufficient",reason="Width is not a strength test.")["versions"][0]
    assert v["reviews"][0]["verdict"]=="Insufficient"
    path.write_text("manual replacement")
    assert call("evidence_history",**loc)["versions"][0]["export"]["status"]=="File differs"
    assert call("brief_read",brief=doc["brief"])["rows"][0]["cells"]["Terms"]["value"]==c["value"]


def test_paired_trial_freezes_expectations_and_reads_real_outputs():
    from pathlib import Path
    def call(a,**k):return execute(dict(project="Matched",action=a,**k))
    cases=[]
    for k,want,text in [("positive","4 h","The installed system lasted 4 h."),("ambiguous","Unknown","The mock-up lasted 8 h."),("missing","Unknown","No trial recorded.")]:
        s=call("capture",title=k,text=text);cases.append(dict(kind=k,row=k,expected=want,edition=s["edition"],quote=text))
    def arm(title,values):
        rows=[dict(name=c["row"],cells={"Finding":{"value":v}}) for c,v in zip(cases,values)]
        b=call("brief_create",title=title,question="Installed runtime?",columns=["Finding"],rows=rows)
        path=call("brief_export",brief=b["brief"])["path"]
        return f"{b['brief']}:1",Path(path)
    base,_=arm("Baseline",["4 h","8 h","Unknown"])
    suite=call("paired_freeze",title="Runtime test",question="Installed runtime?",column="Finding",cases=cases)["suites"][0]["id"]
    good,path=arm("Grounded",["4 h","Unknown","Unknown"]);tie,_=arm("Equivalent",["4 h","Unknown","Unknown"])
    def score(a,b):return call("paired_score",suite=suite,baseline=a,candidate=b)["suites"][0]["results"][0]["decision"]
    assert score(base,good)=="Measured gain" and score(good,tie)=="No measured gain"
    bad,_=arm("Regressed",["Unknown","8 h","Unknown"]);assert score(good,bad)=="Regression"
    with pytest.raises(ValueError):score(good,base)
    path.write_text("changed")
    with pytest.raises(ValueError):score(base,good)


def test_collection_notes_are_local_and_title_survives_reimport():
    def call(p,a,**k):return execute(dict(project=p,action=a,**k))
    args=dict(kind="file",title="draft.csv",origin="/selected/draft.csv",text="A,19")
    src=call("Original","capture",**args);ref=dict(source=src["source"],edition=src["edition"])
    call("Review","discovery_pin",**ref,origin_project="Original")
    for p,note in [("Original","Measured"),("Review","Estimate only")]:call(p,"collection_file",**ref,collection="Read later",note=note)
    with pytest.raises(ValueError):call("Review","collection_retitle",**ref,expected_title="draft.csv",title="Wrong")
    call("Original","collection_retitle",**ref,expected_title="draft.csv",title="Catalogue")
    assert call("Original","capture",**args)["title"]=="Catalogue"
    rows=call("Original","collection_list",all_projects=True)["items"]
    assert {r["note"] for r in rows}=={"Measured","Estimate only"} and {r["title"] for r in rows}=={"Catalogue"}
    assert len({r["digest"] for r in rows})==1
    with pytest.raises(ValueError):call("Review","collection_file",**ref,collection="Read later",note="Lost edit",expected_revision=0)


def test_reporting_condenses_explicit_attribution_without_assuming_independence():
    from hermes_cli.theseus_research.crawl import Page
    def save(title,url,text):return execute(dict(project="Coverage",action="capture",title=title,kind="web",origin=url,text=text))
    root="https://example.org/release/24"
    save("Original",root,"The announcement includes measured totals for several pilots.")
    save("Commentary","https://example.net/report","Original study: "+root+"?utm_source=test")
    p=Page("https://example.org/briefing");p.feed('<a href="/release/24"><b>Press</b> release</a>')
    save("Interview","https://example.org/interview",p.text())
    save("Unknown","https://example.net/other","Navigation: "+root)
    r=execute(dict(project="Coverage",action="reporting_compare"))
    assert [m["title"] for m in r["unclassified"]]==["Unknown"]
    assert {m["title"] for m in r["groups"][0]["members"]}=={"Original","Commentary","Interview"}
    other="This is the same substantial commentary retained at two separate locations."
    save("Copy A","https://example.com/a",other);save("Copy B","https://example.com/b",other)
    r=execute(dict(project="Coverage",action="reporting_compare"));assert any(g["kind"]=="Identical archived text" for g in r["groups"])


def test_decision_revisit_retains_the_original_and_trigger():
    from datetime import date,timedelta
    def call(a,**kw):return execute(dict(project="Supply",action=a,**kw))
    args=dict(title="Order",choice="Use supplier A",reason="Before deadline",assumption="Dispatch in nine days",event="Lead exceeds eleven days")
    d=call("decision_save",**args,due=(date.today()+timedelta(days=7)).isoformat())["decisions"][0]
    assert d["status"]=="Watching"
    d=call("decision_trigger",decision=d["id"],revision=1,note="Lead is now twelve days")["decisions"][0]
    assert d["status"]=="Triggered"
    d=call("decision_review",decision=d["id"],revision=2,outcome="Revised",choice="Seek another offer",note="No longer meets deadline")["decisions"][0]
    assert d["status"]=="Reviewed" and [h["choice"] for h in d["history"]]==["Seek another offer","Use supplier A","Use supplier A"]
    with pytest.raises(ValueError):call("decision_trigger",decision=d["id"],revision=1,note="Duplicate")
    due=call("decision_save",**dict(args,title="Deadline review"),due=date.today().isoformat())["decisions"][0]
    assert due["status"]=="Due"
    with pytest.raises(ValueError):call("decision_review",decision=due["id"],revision=1,note="Wait",outcome="Deferred",due=date.today().isoformat())


def test_audience_release_is_allowlisted_and_revision_bound():
    from pathlib import Path
    def call(a,**k):return execute(dict(project="Confidential board",action=a,**k))
    b=call("brief_create",title="Private plan",question="Margin?",rows=[{"name":"Secret supplier","cells":{"Price":{"value":"49 USD","note":"Private markup 23%"},"Terms":{"value":"15 days < provisional"}}}])
    loc=dict(brief=b["brief"],revision=1)
    with pytest.raises(ValueError):call("audience_export",**loc,public_title="Delivery")
    call("audience_approve",**loc,row="Secret supplier",column="Terms",public_item="Pilot delivery",public_label="Lead time",classification="Estimate")
    result=call("audience_export",**loc,public_title="Delivery & timing")
    ext=Path(result["external"]).read_text();internal=Path(result["internal"]).read_text()
    assert all(x not in ext for x in ("Secret supplier","Private markup","49 USD","Confidential board","Private plan"))
    assert "15 days &lt; provisional" in ext and "Estimate" in ext and "Delivery &amp; timing" in ext
    assert "49 USD" in internal and "Private markup" in internal
    call("brief_edit",brief=b["brief"],expected_revision=1,rows=[{"name":"Secret supplier","cells":{"Terms":{"value":"22 days"}}}])
    assert call("audience_list",brief=b["brief"],revision=2)["approved"]==[]
    with pytest.raises(ValueError):call("audience_export",**loc,public_title="Delivery & timing")


def test_portable_handoff_uses_archived_inputs_and_relative_files():
    import json,hashlib
    from pathlib import Path
    def call(a,**k):return execute(dict(project="Offline",action=a,**k))
    s=call("capture",kind="file",title="Survey",origin="/private/owner/survey.csv",text="Label,Size\nA,2\nB,3\n")
    b=call("brief_create",title="Summary",question="Size?",rows=[{"name":"A","cells":{"Terms":{"value":"2","edition":s["edition"],"quote":"A,2"}}}])
    call("attention_capture",title="Review survey",state="Ready")
    args=dict(brief=b["brief"],revision=1,include_planning=True)
    handoff=call("handoff_export",**args);root=Path(handoff["folder"]);manifest=json.loads((root/"manifest.json").read_text())
    assert all(hashlib.sha256((root/p).read_bytes()).hexdigest()==h for p,h in manifest["sha256"].items())
    assert "Review survey" in (root/"open-work.json").read_text() and "/private/owner/" not in (root/"sources.json").read_text()
    assert call("handoff_export",**args)["folder"]==str(root)
    (root/"brief.csv").write_text("changed detached copy")
    with pytest.raises(ValueError):call("handoff_export",**args)


def test_trail_verifies_history_and_keeps_unattended_time_out_of_resume(monkeypatch):
    from hermes_cli.theseus_research import crawl
    from hermes_cli.theseus_research.library import connection
    calls=[]
    def fetch(url,host,deadline,rules=None):
        calls.append(url)
        if url.endswith("/robots.txt"):return 404,url,"",""
        return 200,url,"text/html",'<p>Read</p><a href="/login">Sign in</a><a href="/authors/iris">Author</a>'
    monkeypatch.setattr(crawl,"fetch",fetch)
    def call(a,**kw):return execute(dict(project="Trail",action="crawl_"+a,**kw))
    run=call("create",url="https://example.org/",question="What is available?",pages=3)["id"]
    call("resume",crawl=run);call("step",crawl=run)
    t=call("checkpoint",crawl=run,question="Which authors mention Iris?",note="Examine the author page")
    assert t["next"]["url"].endswith("/authors/iris") and t["checked"][0]["verified"]
    with connection() as db:db.execute("update crawl_clock set started=started-86400 where crawl=?",(run,))
    call("resume",crawl=run);next_=call("step",crawl=run)
    assert calls.count("https://example.org/")==1 and calls[-1].endswith("/authors/iris") and next_["spent"]<30
    t=call("trail",crawl=run);assert len(t["attempts"])==2 and all(a["outcome"] for a in t["attempts"])
    with connection() as db:db.execute("update editions set body='corrupt' where id=?",(t["checked"][0]["edition"],))
    assert not call("trail",crawl=run)["checked"][0]["verified"]
    with pytest.raises(ValueError):call("resume",crawl=run)


def test_source_editions_page_and_pin_survive_owner_reversion():
    def call(project, action, **fields):
        return execute(dict(project=project, action=action, **fields))

    source_file = dict(kind="file", origin="/selected/record.csv", title="Record")
    versions = [call("Owner", "capture", **source_file, text=str(i)) for i in range(22)]
    source = versions[0]["source"]
    pinned = versions[4]["edition"]
    call("Guest", "discovery_pin", source=source, edition=pinned, origin_project="Owner")
    call("Owner", "capture", **source_file, text="0")
    first = call("Owner", "source_editions", source=source)
    second = call("Owner", "source_editions", source=source, before=first["next_before"])
    all_rows = first["editions"] + second["editions"]
    assert [r["edition"] for r in all_rows] == [v["edition"] for v in reversed(versions)]
    assert [r["edition"] for r in all_rows if r["status"] == "Current"] == [versions[0]["edition"]]
    assert second["next_before"] is None
    guest = call("Guest", "source_editions", source=source)["editions"]
    assert [(r["edition"], r["status"]) for r in guest] == [(pinned, "Pinned")]
    with pytest.raises(ValueError):
        call("Unrelated", "source_editions", source=source)


def test_project_citation_review_protects_context_and_records_decision():
    def call(action, **fields):
        return execute(dict(project="Room contracts", action=action, **fields))

    def capture(text):
        return call("capture", title="Rates", kind="file", origin="/selected/schedule.csv", text=text)

    old = capture("Name,Rate,Basis\nStudio,55,hour\nDesk,20,day\n")
    cells = lambda rate, quote: {"Price": {"value": rate, "quote": quote, "edition": old["edition"], "note": "Confirm booking."}}
    doc = call("brief_create", title="Rooms", question="What are the rates?",
               rows=[{"name":"Desk","cells":cells("20","Desk,20,day")},
                     {"name":"Studio","cells":cells("55","Studio,55,hour")}])
    scope = dict(brief=doc["brief"], expected_revision=1, column="Price")
    cur = capture("Basis,Name,Rate\nhalf-day,Studio,70\nday,Desk,23\n")
    changes = {c["row"]:c for c in call("refresh_review")["changes"]}
    assert changes["Desk"]["state"] == "changed" and changes["Desk"]["quote"] == "day,Desk,23"
    assert changes["Studio"]["state"] == "context changed"
    assert changes["Studio"]["context_changes"] == [{"field":"Basis","before":"hour","after":"half-day"}]
    target = {**scope,"new_edition":cur["edition"],"row":"Studio"}
    with pytest.raises(ValueError,match="context"):
        call("refresh_apply", **target)
    reason = "The brief is about the hourly rate."
    kept = call("refresh_keep", **target, reason=reason)["decision"]
    assert call("refresh_keep", **target, reason=reason)["decision"] == kept
    assert call("brief_read", brief=doc["brief"])["revision"] == 1
    with pytest.raises(ValueError,match="decision changed"):
        call("refresh_reopen", **target, expected_decision=kept["id"]+50)
    opened = call("refresh_reopen", **target, expected_decision=kept["id"])["decision"]
    assert opened["choice"] == "reopen"
    kept2 = call("refresh_keep", **target, expected_decision=opened["id"],reason=reason)["decision"]
    assert kept2["id"] > kept["id"]
    with pytest.raises(ValueError,match="earlier evidence"):
        call("refresh_apply", **target)
    saved = call("refresh_apply", **{**target,"row":"Desk"})
    assert saved["rows"][0]["cells"]["Price"]["value"] == "23"
    assert saved["rows"][0]["cells"]["Price"]["note"] == "Confirm booking."
    assert saved["rows"][1]["cells"]["Price"]["edition"] == old["edition"]
    assert call("refresh_review")["changes"][0]["decision"] == kept2
    newest = capture("Name,Rate,Basis\nStudio,90,week\nDesk,23,day\n")
    assert newest["edition"] != cur["edition"]
    current = {c["row"]:c for c in call("refresh_review")["changes"]}
    assert current["Studio"]["decision"] is None
    with pytest.raises(ValueError):
        call("refresh_keep", **{**target,"expected_revision":2},reason=reason)


def test_project_review_pages_only_live_owned_citations_and_no_guess_on_duplicates():
    def call(project, action, **fields):
        return execute(dict(project=project, action=action, **fields))

    origin = dict(title="Bench table",kind="file",origin="/selected/bench.tsv")
    before=call("Measurement","capture",**origin,text='id\tvalue\nS\t"first\nline"\n')
    cell={"value":"first\nline","quote":'S\t"first\nline"',"edition":before["edition"]}
    call("Measurement","brief_create",title="Runs",question="Which measurement?",
         rows=[{"name":str(i),"cells":{"Result":cell}} for i in range(30)],columns=["Result"])
    call("Pinned","discovery_pin",source=before["source"],edition=before["edition"],origin_project="Measurement")
    call("Pinned","brief_create",title="Shared run",question="What did we receive?",columns=["Result"],
         rows=[{"name":"S","cells":{"Result":cell}}])
    after=call("Measurement","capture",**origin,text='value\tid\n"new\nvalue"\tS\n')
    page=call("Measurement","refresh_review")
    assert page["next_after"] and len(page["changes"])==24
    assert all(item["quote"]=='"new\nvalue"\tS' for item in page["changes"])
    more=call("Measurement","refresh_review",after=page["next_after"])
    assert more["next_after"] is None
    assert {item["row"] for item in page["changes"]+more["changes"]} == {str(i) for i in range(30)}
    assert call("Pinned","refresh_review")["changes"] == []
    assert call("Other","refresh_review")["changes"] == []
    with pytest.raises(ValueError):
        call("Measurement","refresh_review",after=[2,"wrong",""])
    call("Measurement","capture",**origin,text='id\tvalue\nS\t"first\nline"\nS\tconflict\n')
    row=call("Measurement","refresh_review")["changes"][0]
    assert row["state"]=="review" and row["quote"] is None and row["new_edition"]!=after["edition"]


def test_citation_receipt_coverage_and_recovered_decision_history():
    def call(project, action, **fields):
        return execute(dict(project=project, action=action, **fields))

    def capture(project, name, text):
        return call(project, "capture", kind="file", title=name, origin="/selected/"+name, text=text)

    old=capture("Planning","plan.csv","Item,Term\nF,4\n")
    stable=capture("Planning","intake.txt","Check at noon")
    pin=capture("Logistics","lead.csv","Supplier,Lead\nR,6\n")
    call("Planning","discovery_pin",source=pin["source"],edition=pin["edition"],origin_project="Logistics")
    cells={"Terms":{"value":"4","edition":old["edition"],"quote":"F,4","note":"days only"},
           "Offering":{"value":"Noon","edition":stable["edition"],"quote":"at noon"},
           "Price":{"value":"6","edition":pin["edition"],"quote":"R,6"},
           "Open question":{"value":"No signed quote"}}
    doc=call("Planning","brief_create",title="Plan",question="What is the lead time?",rows=[{"name":"F","cells":cells}])
    call("Planning","brief_create",title="Empty",question="Who can confirm?")
    new=capture("Planning","plan.csv","Item,Term\nF,5\n")
    args=dict(brief=doc["brief"],row="F",column="Terms")
    scope=dict(**args,expected_revision=1,new_edition=new["edition"])
    kept=call("Planning","refresh_keep",**scope,reason="Four days is contractual.")["decision"]
    latest=capture("Planning","plan.csv","Item,Term\nF,7\n")
    receipt=call("Planning","refresh_review")
    assert receipt["coverage"]==dict(briefs=2,populated_cells=4,current_local=1,different_local=1,pinned=1,unavailable=0,uncited=1)
    change=receipt["changes"][0]
    assert change["decision"] is None and change["prior_review"]["id"]==kept["id"]
    assert (change["note"],change["question"])==("days only","What is the lead time?")
    assert receipt["source_receipts"]==[dict(source=old["source"],title="plan.csv",
        prior=dict(edition=old["edition"],sha256=old["digest"]),
        current=dict(edition=latest["edition"],sha256=latest["digest"]))]
    history=call("Planning","evidence_history",**args)["citation_decisions"]
    assert history["decisions"][0]["new_edition"]==new["edition"]
    assert history["decisions"][0]["reason"]=="Four days is contractual."
    assert history["next_before"] is None
    assert call("Planning","refresh_history",**args)==history
    assert not call("Planning","refresh_history",**args,before=kept["id"])["decisions"]
    with pytest.raises(ValueError):
        call("Logistics","refresh_history",**args)


def test_retained_wording_withdrawn_or_repeated_in_revised_narrative():
    def call(action, **fields):
        return execute(dict(project="Transit review", action=action, **fields))
    quote="The observation was 14 minutes."
    def capture(text):
        return call("capture",kind="file",title="Correction log",origin="/selected/timing.txt",text=text)
    old=capture("Installed station. "+quote+" Results verified.")
    doc=call("brief_create",title="Commissioning",question="What was measured onsite?",columns=["Finding"],
             rows=[{"name":"Test","cells":{"Finding":{"value":"14 minutes","quote":quote,"edition":old["edition"]}}}])
    latest=capture("Withdrawn from the installed report; the sentence was from a simulation. "+quote+" No onsite result exists.")
    change=call("refresh_review")["changes"][0]
    assert change["state"]=="nearby wording changed" and change["value"] is None
    assert change["nearby"]["prior"]["matches"][0]["before"]=="Installed station. "
    assert "simulation" in change["nearby"]["current"]["matches"][0]["before"]
    with pytest.raises(ValueError):
        call("refresh_apply",brief=doc["brief"],expected_revision=1,row="Test",column="Finding",new_edition=latest["edition"])
    capture("Simulation. "+quote+"\nHistoric proposal. "+quote+"\nWithdrawn: "+quote)
    change=call("refresh_compare",brief=doc["brief"])["changes"][0]
    assert change["state"]=="passage repeated" and change["nearby"]["current"]["more"]
    assert len(change["nearby"]["current"]["matches"])==2
    capture("No elapsed time was saved.")
    change=call("refresh_review")["changes"][0]
    assert change["state"]=="review" and not change["nearby"]["current"]["matches"]
    # An edit outside the bounded window cannot affirm or overturn the claim.
    prefix="Unrelated operator note. "*20
    first=capture(prefix+quote+prefix)
    call("brief_edit",brief=doc["brief"],expected_revision=1,rows=[{"name":"Test","cells":{"Finding":{
        "value":"14 minutes","quote":quote,"edition":first["edition"]}}}])
    capture("Amendment issued elsewhere. "+prefix+quote+prefix)
    change=call("refresh_review")["changes"][0]
    assert change["state"]=="passage retained"
    assert all(len(h[side])<=200 for h in change["nearby"]["current"]["matches"] for side in ("before","after"))


def test_read_tool_project_aliases_are_unique_and_exact_writes_stay_isolated():
    from hermes_cli.theseus_research.plugin import run
    import json
    def search(project):
        return json.loads(run({"project":project,"action":"search","query":"reading"}))
    first=execute(dict(project="River Hall",action="capture",title="First",text="reading 16"))
    assert search("river_hall")["resolved_project"]=="River Hall"
    assert search("river_hall")["sources"][0]["edition"]==first["edition"]
    # Desktop's explicit new-project picker remains exact.
    assert execute(dict(project="river_hall",action="search",query="reading"))["sources"]==[]
    second=json.loads(run(dict(project="River-Hall",action="capture",title="Second",text="reading 88")))
    ambiguous=search("river_hall")
    assert ambiguous["status"]=="ambiguous_project" and set(ambiguous["candidates"])=={"River Hall","River-Hall"}
    assert "sources" not in ambiguous and "reading 16" not in str(ambiguous) and "reading 88" not in str(ambiguous)
    assert search("River-Hall")["sources"][0]["edition"]==second["edition"]
    assert search("River")["sources"]==[]
    from hermes_cli.theseus_research.plugin import run_work
    planning=json.loads(run_work(dict(project="river_hall",action="decision_list",inputs={})))
    assert planning["status"]=="ambiguous_project" and "decisions" not in planning


def test_review_shared_reference_updates_only_the_selected_project_and_preserves_citations():
    def call(p, action, **fields):
        return execute(dict(project=p, action=action, **fields))
    file = dict(kind="file", title="Pickup", origin="/trial/slots.csv")
    def version(time):
        return call("Owner", "capture", **file, text="Route,Time\nEast," + time)
    first = version("06:15")
    for project in ("Team", "Other"):
        call(project, "discovery_pin", source=first["source"], edition=first["edition"], origin_project="Owner")
    private = version("06:50")
    new = version("08:30")
    src = first["source"]
    brief = call("Team", "brief_create", title="Rota", question="East pickup?", columns=["Time"], rows=[{
        "name":"East", "cells":{"Time":{"value":"06:15","quote":"East,06:15","edition":first["edition"]}}}])
    pair = dict(source=src, previous=first["edition"], chosen=new["edition"])
    search = call("Team", "search", query="East")["sources"][0]
    assert search["edition"] == first["edition"] and search["origin_current_edition"] == new["edition"]
    assert search["pin_status"] == "origin uses a different edition" and "08:30" not in search["excerpt"]
    preview = call("Team", "discovery_compare", **pair)
    assert "06:15" in preview["old_text"] and "08:30" in preview["new_text"]
    with pytest.raises(ValueError):
        call("Unknown", "discovery_compare", **pair)
    with pytest.raises(ValueError):
        call("Team", "read", edition=new["edition"])
    assert call("Team", "refresh_compare", brief=brief["brief"])["changes"] == []
    args = dict(**pair, revision=preview["revision"])
    for _ in range(2):
        call("Team", "discovery_adopt", **args)
    assert [s["status"] for s in call("Team", "source_editions", source=src)["editions"]] == ["Pinned", "Previously pinned"]
    assert call("Team", "read", source=src)["text"].endswith("08:30")
    assert call("Other", "read", source=src)["text"].endswith("06:15")
    assert "06:15" in call("Team", "brief_passage", brief=brief["brief"], row="East", column="Time")["text"]
    review = call("Team", "refresh_review")
    assert review["coverage"]["previous_pin"] == 1
    assert [change["new_edition"] for change in review["changes"]] == [new["edition"]]
    change = call("Team", "refresh_compare", brief=brief["brief"])["changes"][0]
    assert change["new_edition"] == new["edition"]
    for project in ("Other", "Team"):
        with pytest.raises(ValueError):
            call(project, "read", source=src, edition=private["edition"])
    with connection() as db:
        assert db.execute("select count(*) from research_pin_changes").fetchone()[0] == 1
    version("06:15")  # Original can return to an earlier edition.
    back = dict(source=src, previous=new["edition"], chosen=first["edition"])
    current = call("Team", "discovery_compare", **back)
    with pytest.raises(ValueError):
        call("Team", "discovery_adopt", **back, revision=0)
    call("Team", "discovery_adopt", **back, revision=current["revision"])
    version("08:30")
    with pytest.raises(ValueError):
        call("Team", "discovery_adopt", **args)  # Stale after a round trip.


def test_pin_preview_handles_long_prefixes_changed_origins_and_damaged_receipts():
    def call(project, action, **fields):
        return execute(dict(project=project, action=action, **fields))
    base = dict(kind="file", title="Log", origin="/trial/report.txt")
    old = call("Origin","capture",**base,text="same "*1300+"one"+" end"*700)
    call("Guest","discovery_pin",source=old["source"],edition=old["edition"],origin_project="Origin")
    new = call("Origin","capture",**base,text="same "*1300+"two"+" end"*700)
    pair = dict(source=old["source"],previous=old["edition"],chosen=new["edition"])
    preview = call("Guest","discovery_compare",**pair)
    assert preview["start"]>0 and preview["next"] and "two" in preview["new_text"]
    latest = call("Origin","capture",**base,text="intervening correction")
    with pytest.raises(ValueError):
        call("Guest","discovery_adopt",**pair,revision=preview["revision"])
    assert call("Guest","read",source=old["source"])["edition"] == old["edition"]
    with connection() as db:
        db.execute("update editions set body='wrong' where id=?", (latest["edition"],))
    with pytest.raises(ValueError, match="receipt"):
        call("Guest","discovery_compare",**{**pair,"chosen":latest["edition"]})


def test_search_reports_full_evidence_separately_from_scope_and_bookmarks():
    def call(action, **args):
        return execute(dict(project="Review", action=action, **args))
    note = call("capture",title="Transfer",text="Scheduled 12 transfers. Completed 7.")
    call("capture",title="Receipt",text="Historical account")
    call("capture",title="Supplier page",kind="web",origin="https://example.org/entry",text="")
    response = call("search",query="transfers")
    assert response["coverage"] == dict(project="Review",scope="current local sources and selected shared editions",
                                        available=3,returned=1,complete_verified=1,extent="partial source text")
    match, = response["sources"]
    assert match["evidence_scope"] == "complete verified edition" and match["excerpt"].endswith("Completed 7.")
    assert "Scheduled" in match["excerpt"] and match["digest"]==note["digest"]
    result=call("search")
    assert result["coverage"]["complete_verified"]==2
    assert next(i for i in result["sources"] if i["kind"]=="web")["evidence_scope"]=="bookmark only"
    with connection() as db:
        db.execute("update editions set body='unsupported' where id=?", (note["edition"],))
    damaged,=call("search",query="transfers")["sources"]
    assert damaged["evidence_scope"]=="edition receipt mismatch" and damaged["excerpt"]==""
    with pytest.raises(ValueError,match="receipt"):
        call("read",edition=note["edition"])


def test_search_complete_evidence_has_a_byte_budget_and_handles_pinned_editions():
    def call(project, action, **args):
        return execute(dict(project=project,action=action,**args))
    long=call("Owner","capture",title="Long",text="π"*610)
    for i in range(6):
        src=call("Owner","capture",title=f"Entry {i}",text="π"*539+str(i))
        call("Guest","discovery_pin",source=src["source"],edition=src["edition"],origin_project="Owner")
    call("Guest","discovery_pin",source=long["source"],edition=long["edition"],origin_project="Owner")
    for project in ("Guest","Owner"):
        result=call(project,"search")
        assert result["coverage"]["available"]==result["coverage"]["returned"]==7
        checked=[r for r in result["sources"] if r["evidence_scope"]=="complete verified edition"]
        assert len(checked)==5 and sum(len(r["excerpt"].encode()) for r in checked)<=6000
        item=next(r for r in result["sources"] if r["source"]==long["source"])
        assert item["evidence_scope"]=="excerpt only"


def test_shared_changes_find_disjoint_edits_including_unicode_and_missing_text():
    def call(project, action, **fields):
        return execute(dict(project=project,action=action,**fields))
    file = dict(kind="file",title="Room notice",origin="/trial/notice.txt")
    old = call("Owner","capture",**file,text="🌿 Door: 18:15\nQuiet room\nKeep this line.\nNorth entry\n")
    call("Guest","discovery_pin",source=old["source"],edition=old["edition"],origin_project="Owner")
    new = call("Owner","capture",**file,text="🌿 Door: 18:45\nQuiet room\nKeep this line.\n")
    pair = dict(source=old["source"],previous=old["edition"],chosen=new["edition"])
    first = call("Guest","discovery_compare",**pair)
    assert (first["change"],first["changes"]) == (0,2)
    assert first["marked"]["old"]["changed"]=="1" and first["marked"]["new"]["changed"]=="4"
    assert "🌿" in first["marked"]["old"]["before"]
    second = call("Guest","discovery_compare",**pair,change=1)
    assert "North entry" in second["marked"]["old"]["changed"] and second["marked"]["new"]["empty"]
    with pytest.raises(ValueError):
        call("Guest","discovery_compare",**pair,change=2)
    full = call("Guest","discovery_compare",**pair,start=0)
    assert full["marked"] is None and full["new_text"]==call("Owner","read",source=new["source"])["text"]


def test_bounded_change_index_keeps_large_originals_pageable():
    from hermes_cli.theseus_research.discovery import change_ranges, marked_passage
    assert change_ranges("common\n"*2201,"common\n"*2200+"end\n") is None
    assert change_ranges("a\n","a\nb\n")==[(2,2,2,4)]
    old="a"*3000
    passage = marked_passage(old,0,len(old))
    assert passage["continues"] and len(passage["changed"])==2400


def test_exhaustive_search_receipt_requires_all_selected_editions_in_full():
    def lookup(query=""):
        return execute(dict(project="Notes",action="search",query=query))["coverage"]["extent"]
    assert lookup()=="no selected sources"
    for text in ("West entry opens at seven.","South entry closes at ten."):
        execute(dict(project="Notes",action="capture",title=text[:4],text=text))
    assert lookup("West")=="partial source text"
    assert lookup("entry")=="exhaustive verified source text"
    execute(dict(project="Notes",action="capture",title="Long",text="entry "*800))
    assert lookup()=="partial source text"
