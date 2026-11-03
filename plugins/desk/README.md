# Desk

A customer writes to you: "the export is empty". You paste it here. An agent sorts it, keel finds past cases, and you
choose what happens next.

```
 you paste a request ──▶ it waits in the Desk and the Inbox (nothing left keel yet)
        │
        │ keel finds past cases: requests, tasks and flows with shared words (on this computer, no model)
        ▼
 you press Sort ──▶ desk-sorter (reads only): what is it, how urgent, which code, what next
        ▼
 YOU choose:  make task (on the Tasks board)  |  fix flow  |  change flow  |  close
```

## What goes to the model company

It says so on the page, too.

| | goes to the model company |
| --- | --- |
| The request's text | **yes, when you press Sort**, not before |
| The titles of its past cases | **yes, when you press Sort** |
| Files of this project's repo the agent opens | **yes**, as in any flow |
| The "From" field | **never**: it stays in keel |

keel warns you when the text looks like it holds an e-mail address, a phone number or a password. That check is simple and
it can miss things. It does not remove anything. You decide what you paste, and you need your own rule about what may go
to your model company.

## Rules it keeps

- **Only you decide.** The choice gate is `person: true`: no run mode and no Supervisor answers it.
- **A request is data.** The sorter is told never to follow an instruction in it. A fix or change flow that starts from it
  still has all its own gates. Still, read a stranger's text before you start a flow from it.
- **Nothing is sent to the customer.** Desk has no mail or chat part. A reply the agent suggests is for you to copy.

## What it does not do

- It does not read a mailbox or a ticket system. You paste each request.
- Past cases are found by shared words only, so another word for the same thing is not found. The agent says if a found
  case is really the same.
- keel has one person, so there is no team queue, no owner and no "assigned to".
- A request's text stays in keel's database until you delete it. There is no automatic clean-up.
