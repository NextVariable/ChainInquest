# ChainInquest closed beta tester kit

Use this kit for 3–5 invited testers. One facilitator, one 30–40 minute session
per tester, and the same tasks for everyone. Internal engineering runs must not
be counted as user metrics.

## Before the session

- Confirm the tester received a bearer token over a separate channel from the
  URL. Never ask a tester to paste a private key, seed phrase or exchange API key.
- Confirm the deployed origin matches `CHAININQUEST_BETA_ORIGIN` and the service
  passes the verification section in `deploy/README.md`.
- Create a fresh tester name in the token map; do not reuse another tester's
  account.

## Consent and scope (read aloud)

> ChainInquest runs read-only queries against public chain data for contracts
> you specify. It does not sign transactions and does not need wallet access.
> Contract ownership is declared by you using a public source; the tool verifies
> on-chain code and activity, not legal ownership. Your investigation inputs and
> feedback are stored on the beta host for the retention window. Do not enter
> secrets or personal data. Missing data is reported as unknown, not zero.

## Tasks

Record completion, duration and any confusion. Do not coach unless the tester is
blocked for more than two minutes; note where they stopped.

1. **First impression.** Show the landing page only. Ask: what do you think this
   product does, and who is it for? Record the answer before explaining anything.
2. **Create an investigation.** Ask the tester to enter a project name, one or
   two contract addresses, the HTTPS page that lists them, a goal and a scope.
   Use the built-in Circle example only if they cannot supply a project.
3. **Review the plan.** Ask them to read the preview and explain what will run.
   Then ask what "Declared attribution: confirmed" means. The expected answer is
   that the requester cited a source and ChainInquest did not verify ownership.
4. **Run and read the result.** Ask them to start the investigation and explain
   the difference between complete, partial and unknown, and what an evidence gap
   means. Correct answers must not treat unknown as zero activity.
5. **Check evidence and export.** Ask them to open the evidence details, download
   JSON and HTML, and say which file they would share with a colleague.
6. **Feedback.** Ask them to submit the in-product feedback form without help.

## Feedback record

The product stores four closed answers plus an optional comment per job:

| Field | Allowed values |
| --- | --- |
| Investigation completed | yes / partly / no |
| Understood the result | yes / partly / no |
| Incomplete data was clear | yes / no / unsure |
| Would reuse | yes / maybe / no |
| Comment | up to 500 characters |

Ask these follow-up questions and record them separately:

- What did you expect the tool to do that it did not do?
- Where did you hesitate, and what did you think was happening?
- Was any number or label misleading? Which screen?
- Would you use this for a real review next month? Why or why not?

## Metrics to report

Report per tester: task completion (1–6), elapsed time, answer correctness for
tasks 1, 3 and 4, the four closed answers, and verbatim quotes. Label the source
as invited tester feedback. Do not merge internal runs, demo runs or repeated
runs by the facilitator into the tester count.

## Stop criteria

Pause the beta and preserve evidence if any of these occur:

- one tester can see another tester's job, result or feedback;
- incomplete coverage is shown as a proven zero;
- a state or resume file corruption cannot be recovered by restarting;
- credentials, tokens or request bodies appear in logs;
- retention deletes a running or locked job, or a path outside the store.

## After the session

- Export the feedback JSON before any retention run.
- Revoke the tester token if the session is finished.
- File defects as GitHub issues with the job ID and time, never with the token.
