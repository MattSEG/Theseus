import json
import sqlite3

from .library import execute

DESCRIPTION = ("Use the local research library when asked to capture material into a project, "
               "search its saved sources or open an archived edition. For ordinary saved-source questions use search first. A complete verified edition contains the entire checked text in excerpt. If it answers the question, cite it directly; read only to resolve a gap. Coverage counts describe this project library only. To find earlier versions of a selected source, use source_editions and its optional next_before cursor as before. Capture only material selected for saving. When capture returns affected_briefs, report relevant changed values or conditions from that receipt. An offered comparison awaits the user. Change saved claims only when asked. "
               "A web bookmark with empty text records a URL only. When asked to reimport a project file, search for its source. If file_linked, use refresh_file with source and expected_edition. It reads the actual selected file and returns verified_file. A file capture without a link archives supplied text and does not verify the original bytes. For a byte-verification question, use file_proof on the relevant edition. Its match covers import time, while archive only leaves the original comparison unknown. "
               "For an explicitly requested bounded same-site crawl, create it with url, question, pages, depth and seconds; resume and step until complete or budget reached. Supply the crawl ID for each operation. Inspect crawl_trail before resuming and record the outstanding question with crawl_checkpoint. Use source and edition IDs to cite or read evidence; use next_start for long documents.")
SCHEMA = {"name":"research_library","description":DESCRIPTION,"parameters":{
    "type":"object","required":["action","project"],"additionalProperties":False,"properties":{
        "action":{"type":"string","enum":["capture","refresh_file","search","read","source_editions","projects","crawl_create","crawl_resume","crawl_step","crawl_pause","crawl_status","crawl_list","crawl_trail","crawl_checkpoint"]},"project":{"type":"string"},
        "title":{"type":"string"},"kind":{"type":"string","enum":["file","web","note"]},
        "origin":{"type":"string"},"text":{"type":"string"},"query":{"type":"string"},
        "expected_edition":{"type":"integer"},"before":{"type":"integer"},"source":{"type":"integer"},"edition":{"type":"integer"},"start":{"type":"integer"},"url":{"type":"string"},"question":{"type":"string"},"crawl":{"type":"integer"},
        "note":{"type":"string"},"depth":{"type":"integer"},"pages":{"type":"integer"},"seconds":{"type":"integer"}}}}


def run(args, session_id=None, **_):
    try:
        if args.get("action") in ("search","brief_export") and not session_id:
            from gateway.session_context import bound_session_key, get_session_env
            if bound_session_key():
                session_id = get_session_env("HERMES_SESSION_ID")
        return json.dumps(execute(args, resolve_tool_reads=True, session_id=session_id),ensure_ascii=False)
    except (ValueError, OSError, sqlite3.Error) as exc:
        return json.dumps({"error":str(exc)})


BRIEF_DESCRIPTION = ("Build or edit a saved comparison brief for a research project. For creation use title, question, "
    "template (Market comparison, Scientific review, Personal decision) and optionally custom columns and rows. "
    "Rows are objects with name and cells keyed by exact column names, e.g. {name:'Supplier',cells:{Price:{value:'68 USD',edition:2,quote:'68 USD per unit'}}}. "
    "Use only real original quotes; include personal annotations in note. Unknown cells may be blank. On edit supply brief ID and expected_revision. "
    "For a revised source or project-wide citation check, start with refresh_review. It returns coverage, the brief questions and notes, paired passages, changed conditions, edition digests and earlier review decisions. Use that receipt directly when sufficient. Fetch more evidence for specific gaps, and pass next_after as after for more comparisons. Use refresh_history for earlier keep/reopen decisions. Compare one brief with refresh_compare. If another field changed in a cited record, review the conditions before revising the claim. A retained passage proves only that the wording remains. When explicitly requested, keep an earlier citation with refresh_keep and a reason, or reopen with refresh_reopen. Send expected_decision if a prior decision exists. Only apply a reviewed change using row, column, new_edition and expected_revision. Read older revisions or export a real CSV. Only capture or change records when the user asks.")
BRIEF_SCHEMA = {"name":"research_brief","description":BRIEF_DESCRIPTION,"parameters":{"type":"object",
    "required":["action","project"],"properties":{
    "action":{"type":"string","enum":["brief_list","brief_read","brief_create","brief_edit","brief_export","refresh_compare","refresh_apply","refresh_review","refresh_keep","refresh_reopen","refresh_history"]},
    "project":{"type":"string"},"brief":{"type":"integer"},"revision":{"type":"integer"},"expected_revision":{"type":"integer"},
    "template":{"type":"string"},"title":{"type":"string"},"question":{"type":"string"},"columns":{"type":"array","items":{"type":"string"}},
    "rows":{"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"cells":{"type":"object","additionalProperties":
        {"type":"object","properties":{"value":{"type":"string"},"note":{"type":"string"},"edition":{"type":"integer"},"quote":{"type":"string"}}}}}}},
    "row":{"type":"string"},"column":{"type":"string"},"new_edition":{"type":"integer"},"add_column":{"type":"string"},"rename_column":{"type":"string"},"new_name":{"type":"string"}}}}


BRIEF_SCHEMA["parameters"]["properties"]["after"] = {"type":"array","minItems":3,"maxItems":3,"items":{"anyOf":[{"type":"integer"},{"type":"string"}]},"description":"The previous project review page cursor."}

BRIEF_SCHEMA["parameters"]["properties"].update({"reason":{"type":"string"},"expected_decision":{"type":"integer"},"before":{"type":"integer"}})

WORK_ACTIONS = ["scenario_save","scenario_list","analysis_columns","analysis_run","analysis_list","analysis_record"]
WORK_ACTIONS += ['workflow_list', 'workflow_save', 'workflow_start', 'workflow_advance']
WORK_ACTIONS += ['attention_list', 'attention_capture', 'attention_change']
WORK_ACTIONS += ['discovery_search', 'discovery_pin', 'discovery_list']
WORK_ACTIONS += ["preference_list","preference_accept","preference_undo"]
WORK_ACTIONS += ['evidence_history', 'evidence_review']
WORK_ACTIONS += ['paired_list', 'paired_freeze', 'paired_score', 'paired_export']
WORK_ACTIONS += ['collection_list', 'collection_file', 'collection_retitle']
WORK_ACTIONS += ['reporting_compare']
WORK_ACTIONS += ['project_next','analysis_preview']
WORK_ACTIONS += ['decision_list', 'decision_save', 'decision_trigger', 'decision_review']
WORK_ACTIONS += ['audience_list', 'audience_approve', 'audience_remove', 'audience_export']
WORK_ACTIONS += ['handoff_list', 'handoff_export']
WORK_DESCRIPTION = ("Use the same local calculation and planning records as the Theseus desktop. "
    "For what to do next, what fits a time budget, or where work paused, project_next checks this project’s commitments, due decisions, workflow checkpoints and paused web research together. Pass max_minutes when supplied. Use an existing planning receipt when it already covers the request. "
    "For a one-batch scenario, pass title, currency, price and cost per unit, quantity, fixed upfront costs, "
    "lead_days, supplier_days from order and customer_days after delivery in inputs. A cost citation uses edition and quote. "
    "If an import returns analysis_suggestion, offer its descriptive range preview when relevant. Use analysis_preview with source, edition and sha256 for a requested read-only overview. For grouped CSV analysis use source, title, measure, group (optional) and unit. Read columns first. Reruns can specify an archived edition. Inspect extrema using run and record. Save a workflow from a completed brief with title, steps (one per line), output and review. Start using workflow ID, new title and question. Advance with job, expected_step and note. Capture commitments with title, state (Ready, Needs decision, Waiting or Done), optional minutes, context and source. For an explicitly cross-project review pass all_projects=true with filter and max_minutes. Only search other projects when requested. Pin a selected source and edition with origin_project to reuse it locally. Inspect repeated populated brief exports with preference_list. Set or undo a project layout only when asked using template and fingerprint. Inspect a changed cell with brief,row,column. For explicit support review supply revision, layer (Observation, Comparison, Conclusion), verdict (Supports, Insufficient, Contradicts) and reason. For manually requested matched categorical trials, freeze title, question, column and three cases with kind, row, expected, edition, quote; then score distinct baseline and candidate ID:revision CSVs. File an accessible source and edition under collection with a project note. Update the shared title only from the owner project using expected_title. Cross-project collection review requires all_projects=true. Compare repeated reporting in the selected project using reporting_compare and an optional query. Grouped references demonstrate shared attribution, not independent evidence. Watch a decision with title,choice,reason,assumption,due and/or event. Explicitly trigger with a note or review as Kept, Revised, Deferred using decision, revision and note. Audience exports use only explicitly approved public cells from the current brief revision. Approval requires row,column,public_item,public_label and classification; export with a public_title. Build an explicitly internal portable folder from selected brief+revision or measurement run. Include outstanding work only if requested. List existing records before creating a duplicate. Only save on request.")
def run_work(args, **kwargs):
    fields=args.get("inputs",{})
    if not isinstance(fields,dict) or args.get("action") not in WORK_ACTIONS:
        return json.dumps({"error":"Choose a supported planning operation and give its inputs as an object."})
    return run(dict(fields,action=args.get("action"),project=args.get("project")),**kwargs)

def desktop_request(params, **_):
    if params.get("action") in {"analysis_suggestions","analysis_keep_preview"}:
        from .library import connection,field
        from .table_assist import operate
        with connection() as db:
            return operate(db,field(params,"project",100),params)
    if params.get("action") in {"capture_selected_file","file_list","file_revoke"}:
        from .local_files import desktop
        return desktop(params)
    if params.get("action") in {"impact_mode","impact_recent","impact_dismiss","impact_check"}:
        from .source_impact import desktop
        return desktop(params)
    if params.get("action") in {"assist_followups","assist_chat","assist_project","assist_set_mode","assist_check","assist_dismiss","assist_evidence","assist_apply"}:
        from .assistance import desktop
        return desktop(params)
    if params.get("action") in {"search_trial_freeze","search_trial_replay","search_trial_arm","search_trial_stop","search_trial_list","search_trial_export_check","search_trial_configure","search_trial_scan_exports"}:
        from .library import connection, field
        from .search_trials import operate
        with connection() as db:
            return operate(db,field(params,"project",100),params)
    if params.get("action") in {"pilot_status","pilot_start","pilot_resume","pilot_apply","pilot_restore"}:
        from .pilot import action
        selected = None
        if params["action"] == "pilot_start" and params.get("review") is not None:
            selected = {k:params.get(k) for k in ("project","review","digest")}
        return action(params["action"],params.get("expected"),params.get("follow_up",False),params.get("head"), selected)
    if params.get("action") in {"prediction_freeze", "prediction_list"}:
        from .library import connection, field
        from .followups import operate
        with connection() as db:
            return operate(db, field(params, "project", 100), params)
    return execute(params)


def register(ctx):
    ctx.register_rpc("research", desktop_request)
    ctx.register_tool(name=SCHEMA["name"],toolset="memory",schema=SCHEMA,handler=run,description=DESCRIPTION)
    ctx.register_tool(name="research_brief",toolset="memory",schema=BRIEF_SCHEMA,handler=run,description=BRIEF_DESCRIPTION)
    ctx.register_tool(name="research_planning",toolset="memory",handler=run_work,description=WORK_DESCRIPTION,
        schema={"name":"research_planning","description":WORK_DESCRIPTION,"parameters":{"type":"object",
        "required":["action","project"],"properties":{"action":{"type":"string","enum":WORK_ACTIONS},
        "project":{"type":"string"},"inputs":{"type":"object","additionalProperties":True}}}})
