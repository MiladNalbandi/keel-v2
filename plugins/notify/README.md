# Notify

keel tells you in chat or by mail that a flow waits for you. You open keel and answer in the Inbox. You cannot answer from
the message.

```
 a flow reaches a gate ──▶ keel's Inbox (as always)
                  │
                  └──▶ Notify ──▶ the channels YOU added ──▶ your chat or mail
```

## Set it up

Settings › **Notify** › Add a channel.

- **Chat webhook:** the "incoming webhook" address from your chat service. Slack, Mattermost and others read the field
  `text`; Discord reads `content` (keel picks it from the address).
- **Mail:** your mail server, port, security (STARTTLS or TLS), From and To.

Press **Send a test** to see it work.

## What leaves keel

Only this: the project's name, the flow's title, the gate's name and where to answer.

> keel: a flow waits for you in shop. Export invoices: spec approval. Answer it in keel's Inbox.

Never a gate's text, code, a diff or a file. But a flow's **title** is your own words, so it goes out too. Do not put
secrets or customer data in a title if your chat service should not see them.

## Rules it keeps

- **No channel, no message.** A new keel sends nothing outside.
- **Secrets stay in keel.** The webhook address and the password are encrypted in keel's secret store. keel shows them
  to nobody, also not to the page that saved them.
- **No plain text over a network.** A webhook over the internet must use https. A mail server needs TLS or STARTTLS; only a
  server on the same computer may use none.
- **It stays quiet when you asked for quiet.** A project with notifications `none` sends nothing; `needs_you` sends only
  what waits for you.
- **It cannot flood you.** The same question goes out once, and a channel sends at most 30 messages an hour. The rest wait
  in the Inbox.
- A gate that keel's run mode or the Supervisor answers by itself sends nothing: nobody has to do anything.

## What does not work yet

- **It covers flows' gates and failed flows.** Other things in the Inbox (a task's question, a plugin install request, a
  KeelBot command that waits for your OK) send no message yet.
- With a Supervisor on, a gate it then answers can still have sent a message first.
- You cannot answer from chat. That is on purpose: answering needs keel's sign-in.
- keel has one person, so all channels are that person's. There is no "send this to the owner of the flow".
- It was tested against a local stand-in webhook and a stand-in mail server, not against a real Slack, Discord or mail
  service. STARTTLS and TLS have not run against a real server.
