import json
from datetime import datetime, timezone

from grok_usage import iter_cli_usage


def test_completed_prompts_reconcile_cache_and_reasoning_and_deduplicate(tmp_path):
    start = datetime(2024, 1, 5, 12, tzinfo=timezone.utc)
    end = datetime(2024, 1, 5, 13, tzinfo=timezone.utc)
    usage = dict(totalTokens=80, inputTokens=60, outputTokens=20,
                 cachedReadTokens=41, reasoningTokens=14)
    update = dict(sessionUpdate='turn_completed', prompt_id='fake-prompt',
                  usage=dict(usage, modelUsage={'grok-4.7-build': usage}))
    event = dict(timestamp=start.timestamp(), params=dict(sessionId='fake-session', update=update))
    for project in ('project', 'copy'):
        session = tmp_path / project / 'session'
        session.mkdir(parents=True)
        (session / 'updates.jsonl').write_text('\n'.join(map(json.dumps, [
            dict(event, params=dict(event['params'], update=dict(update, usage=None))),
            event,
            dict(event, timestamp=end.timestamp(), params=dict(event['params'], update=dict(update, prompt_id='outside'))),
            dict(event, params=dict(event['params'], update=dict(update, sessionUpdate='agent_message_chunk'))),
        ])) + '\n{"partial": "turn_completed"')

    issues = set()
    [r] = list(iter_cli_usage(start, end, root=tmp_path, on_issue=issues.add))
    assert (r.model, r.input, r.cache_read, r.output, r.total) == ('grok-4.7-build', 19, 41, 20, 80)
    assert issues == {'Grok CLI completion lacks a consistent token split'}
