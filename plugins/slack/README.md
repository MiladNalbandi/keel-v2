# Slack

From Slack you can ask how a task or flow goes, answer a gate and make a task. keel connects out to Slack (Socket Mode), so a keel on a laptop needs no
public address.

```
 keel ──connects out (Socket Mode)──▶ Slack
   ▲                                    │
   │  a button, or a message to the app │  only a member on your list; never a bot
   ▼                                    │
 same check as keel's Inbox ◀───────────┘
```

**This has never run against the real Slack.** There is no workspace or token where it was built. All its tests use a local stand-in server that speaks the
same protocol as far as keel uses it. The first real use may need a fix. The free chat was checked once with a real model, on the stand-in: it answered about the project, and when asked to delete a file, commit and push, it did none of them.

## What you can do

Send the app a direct message, or mention it in a channel:

| You write | What happens |
| --- | --- |
| `status` | what runs and waits now, per project |
| `status export` | each flow or task with these words: its status, step, criteria, tokens |
| `waiting` | what waits for you, with a code for each |
| `answer ab12cd-9f3e approve` | answers that gate. With a reason: `answer ab12cd-9f3e reject add a test first` |
| `task Fix the export` | a task on the Tasks board. Also `task Fix it \| more words` and `task in shop: Fix it` |
| `task Fix the export @sara` | the same, with Sara's name on it. She gets a direct message. The name must be on your list |
| `assign late filter @sara` | puts that name on a task: its title, the start of its id (Slack shows it), or a key |

When a flow waits at a gate, a message comes to your channel, with a button per answer. When the Supervisor does not answer a gate, it says why under that message.
When the gate is answered anywhere, the message loses its buttons and says who answered.

A gate with several questions has a button, **Answer the form**. It opens a Slack form with the same options as keel's Inbox, and the answer has the same check. Slack cannot add a box when you tick something, so a skip menu has one box under it: one line per ticked step, `lint: no linter here`. A form Slack
cannot show (too many questions or options, a very long option) says *answer it in keel*.

**A name on a task is a label, not an account.** keel has one person: `@sara` writes a name and sends a message. It gives Sara no rights in keel. A task Jira updates keeps Jira's assignee: Slack says so and changes nothing.

### More, if you turn it on

Each of these is **off** until you turn it on in Settings › Slack.

**Let people ask keel in Slack.** A message that is no command goes to a model, and the answer comes in the same thread. A reply in that thread goes on. The model can read the
project's files and keel's status lines, and nothing else. keel enforces that in code, for every kind of model: it cannot write a file, run a command that changes files or git, commit, push,
start a flow or answer a gate. Asked to, it says which command does that. One question at a time per person, and a daily cap per person (dollars and tokens: a model on a
plan reports no cost). Its tokens count on the Budget page. To continue in a channel without a mention, the app needs one more scope; see below.

**Let people start flows from Slack.** `start change in shop: add a late filter`, or `start change add a late filter`. keel answers with one button, **Start it**, which only you can press, for 10 minutes.
It is the dashboard's own start, with the budget and caps. The record says `slack:<name> (<member id>)` and Record shows it. The flow's gates then come to the channel.

**Allow every gate from Slack.** A warning first: Slack is not keel's sign-in. Someone who gets into one of these Slack accounts could approve a new dependency, the final review or the pull
request. With this on, gates only a person answers and the Supervisor's never list get buttons, for people on a second, shorter list (**who may answer these**, each also on the first list). A
button opens a form that asks for a reason, and sending it is the one confirm. The Supervisor's own limits do not change: it still never answers these. **Budget and secret gates stay in keel, whatever you set.**

## Set it up

You make your own Slack app, in your own workspace:

1. <https://api.slack.com/apps> › Create New App › From scratch.
2. **Socket Mode**: turn it on. Make an app-level token with the scope `connections:write`. It starts with `xapp-`.
3. **OAuth & Permissions › Bot Token Scopes**: `chat:write`, `app_mentions:read`, `im:history`.
4. **Event Subscriptions**: turn on. Under *Subscribe to bot events*, add `app_mention` and `message.im`.
   Only for a chat that goes on in a channel's thread without a mention: also `message.channels`, with the scope `channels:history`. Slack then sends keel every message in those channels; keel reads only replies in its own chat threads and drops the rest, with no model.
5. **Interactivity & Shortcuts**: turn on (the buttons need it). No Request URL is needed in Socket Mode.
6. **App Home**: under Show Tabs, allow people to send messages to the app.
7. **Install App**: install it. Copy the bot token. It starts with `xoxb-`.
8. In Slack, make or pick a channel and invite the app: `/invite @your-app`. Copy the channel id (channel details, at the bottom).
9. In keel, Settings › **Slack**: paste both tokens, the channel id, and one line per person who may act: their member id (profile › More › Copy member ID) and a name.

The state should say *connected*. Then message the app: `status`.

## Rules it keeps

- **Only people on your list.** Anyone else is told no and nothing happens. A message from a bot is never read. An event from another workspace is refused.
- **The same check as the Inbox.** An answer goes through the same call as keel's Inbox. A wrong choice or a missing reason is refused the same way. A button carries its question's id and a
  code holds part of it, so an old message can never answer a newer question.
- **Some gates stay in keel.** Budget and secret gates, always. Unless you turn on **Allow every gate from Slack**, also a gate with `person: true` (Answers, Desk, Goals), and everything on the Supervisor's never list: new dependencies, read-only commits, notes only a person
  writes, the final review, the pull request. Slack tells you about them and has no buttons for them. This is stricter than it must be: the person on Slack is a person. It was chosen as the safe default, so the
  owner can overrule it.
- **Slack's words are data.** Commands are matched on this computer, with no model. Slack text reaches an agent only as a reason or a form's words at a gate, a task's text in a flow, what you write after `start`, and (when you turned it on) the chat, where it sits in a block marked as data, for an agent that can only read. No Slack text widens what an agent may do.
- **It records who.** The gate's record says `slack:<name> (<member id>)`, and Record and the Flow page show it came from Slack. Settings › Slack lists what happened, refusals too.
- **Tokens stay in keel's secret store.** Nothing shows them. They go only to Slack over https.
- **Titles cannot ping.** A title cannot mention `@channel`, make a link or change the look.

## What goes where

It says so on the page, too.

| | |
| --- | --- |
| **To Slack** | project names, flow and task titles, status, a gate's name and short lines, the Supervisor's words about a gate, token counts. A gate's whole text only if you turn that on: it can hold code |
| **To the model company** | a reason or a form's words at a gate, a task's text in a flow, what you write after `start`. With the chat on: each message that is no command, the thread's earlier messages, keel's status lines, and the project's files the model chooses to read |

Everyone in the channel can read the questions. Only people on your list can answer.

## What it does not do

- One workspace per keel.
- A form's mistake cannot be shown inside the Slack form: keel's check runs after the form closes, and its answer comes as a message in the thread. Press the button again to answer.
- The chat runs on the project's default model. It cannot see other projects' files unless you ask in that project (`in shop: ...`).
