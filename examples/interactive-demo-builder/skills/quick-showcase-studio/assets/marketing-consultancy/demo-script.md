## Setup & Context

> **SOHO** = Small Office / Home Office — micro-businesses of 1 to 10 people (freelancers, remote professionals, consultants, independent contractors) running business-critical work from a home office or very small commercial space.

**Persona:** You are Darla Emiliano, founder of AnyCompany SOHO Studio, a 2-person marketing and brand consultancy you run from your home office. You recently brought on your first hire, Diego Ramírez, a part-time associate marketer. Together you serve 5 retainer clients, have 1 project in delivery, and 2 prospects in the pipeline.

**Narrative arc:** This demo follows a single day-in-the-life story — Darla's Monday morning. It's not a feature tour; each moment builds on the last. The audience sees how Quick synthesizes scattered data from email, chat, calendar, and financials into prioritized actions for a micro-business owner.

**Cast:**

- **Darla Emiliano** — Founder (you)
- **Diego Ramírez** — First hire, part-time associate marketer
- **Sam Whitfield** — Owner, Harbor Point B&B (prospect → new client)
- **Priya Patel** — Owner, Bloom & Vine Florals (retainer client)
- **Marcus Lindqvist** — Design subcontractor
- **Jordan** — Copywriter subcontractor
- **Marcus Bell** — Contact, Foundry Coffee Roasters (retainer client)
- **Jonah Feldman** — Partner, Cedar & Co. Law (retainer client)
- **Dr. Ana Rivera** — Owner, Rivera Family Dental (retainer client)
- **Dr. Neel Shah** — Owner, Lumen Pediatrics (new discovery lead)
- **Tess Okoro** — Owner, Northside Yoga (retainer client)
- **Jake Torres** — Owner, Grid Street Bikes (project client)

---

## Opening: Darla Connects Her World

*Frame the day. The reason Quick can be this useful by 8 AM is that it already works from Darla's real email, calendar, messages, and numbers. Open by showing that connecting took about two minutes — no files to hand over, no data migration.*

### What to say:

> "Everything you're about to see runs on Darla's own tools. She connected them once, in a couple of minutes, and hasn't thought about it since."

### What to show:

**Primary path (do this live):** In Quick, open **Customize** in the left sidebar and select **Connectors**. Browse the catalog live so the audience sees the breadth of what Quick connects to: Communication & Collaboration, Project Management, Knowledge & Productivity, Data & Analytics, Sales & Marketing, Finance & HR. Narrate: *"These are the built-in integrations: email, calendar, Slack, Teams, invoicing, project management, and more. You browse them right here under Customize, and each one is a click to authorize. Darla connected her email, calendar, and team channel in under two minutes."*

> **Backup:** If you cannot show the live connectors catalog (no connection, restricted environment), just describe it: name the connector categories above and make the point that each is a click to authorize. If your team keeps a connectors slide for this deck, show that; the built-in showcase does not ship one, so don't rely on a file being in your session.

> **Demo note:** This is a demo environment, so we will not walk through the live OAuth flow. Browsing the catalog under Customize is enough to make the point; you do not need to actually authorize a new connector on stage.

**Then show Knowledge (do this live too):** Still under **Customize**, move to the next tab, **Knowledge**. Show that Darla has allowed Quick to view a few folders on her computer, under Local folders. Narrate: *"Beyond the connected apps, Darla also let Quick see a couple of folders right on her computer. That is how Quick can work from her real files, not just her email and chat, and she stays in control of exactly which folders are shared."*

Then go straight to the Activity Feed: *"Everything you're about to see in her morning brief is the payoff of that setup: a couple of connected apps and a couple of folders, all done in minutes."*

### Optional confirming prompt:

"What are you connected to right now, and what can you see across my email, calendar, and messages?"

**Expected output:** A short confirmation of connected sources and a one-line sense of what's available (today's calendar, unread mail, recent messages, current receivables) — proving the connection is live.

### Talking Points:

- Keep this brief — it's the setup, not the show. The point to land: connecting was quick and path-neutral, a couple of clicks, no data migration
- The audience needs to understand that the morning triage they're about to see is only possible *because* the connection was this effortless
- For audiences who care about security: Quick reads from these tools with the user's own permissions — it doesn't copy or store their data separately

---

## Act 1: Morning Triage — Email Response (Cross-Channel Synthesis)

### What the audience sees:

The Activity Feed shows a busy Monday morning. At the top, the three urgent/important items:

1. **🔴 Harbor Point B&B — Proposal Accepted** (urgent) — Sam Whitfield replied: they want to move ahead, launch in 6 weeks, asking about timeline and annual discount
2. **🔴 2 Invoices Overdue — $4,300 Outstanding** (urgent) — Cedar & Co. Law ($2,200, 12 days) and Grid Street Bikes deposit ($2,100, 6 days)
3. **🟡 Bloom & Vine — Rush Instagram Post** (important) — Priya needs a caption and story for tomorrow, same-day wedding order

Below those, the day plan shows 3 meetings: Weekly Planning & Diego Sync, Rivera Dental Check-in, and Grid Street Strategy Work. Plus background items from Diego, Marcus Lindqvist, Dr. Sánchez, Foundry Coffee, Dr. Rivera, Tess, a payment notification, Design Suite renewal, and the Chamber speaking invitation.

### Demo Action:

Click on the **"2 Invoices Overdue"** card → select **"Draft reminders"** CTA.

**Prompt:** "Draft polite but firm payment reminders for both overdue invoices. Reference the invoice number, amount, and how many days overdue. Warm for Cedar & Co. since they're a good ongoing client; a little firmer for the Grid Street deposit since the project is already underway."

### Expected Result:

Quick pulls the invoice details from the feed item and KG context about the client relationships, then generates:

---

Here are your two payment reminders, tailored for each relationship:

**To: Jonah Feldman, Cedar & Co. Law** Subject: Friendly reminder — Invoice #2041

Hi Jonah,

Hope you're having a great start to the week. Just a quick note — Invoice #2041 for $2,200 is currently 12 days past its Net 15 terms. I know things get busy, so this may have just slipped through. Would you mind taking a look when you get a chance?

Happy to resend the invoice if that's easier. Looking forward to our monthly call later this week.

Best, Darla

**To: Jake Torres, Grid Street Bikes** Subject: Deposit balance — Invoice #2038

Hi Jake,

Quick heads-up — the deposit balance for your brand project (Invoice #2038, $2,100) is now 6 days past due. Since the project is already underway and Marcus Lindqvist is starting the moodboard this week, clearing this will help us keep everything moving on schedule for the mid-month delivery.

Let me know if you have any questions or need me to resend the invoice.

Thanks, Darla

---

### Talking Points:

- Quick wrote both emails in seconds, with the right numbers pulled from invoicing data
- It adapted tone per relationship — warm for the long-term retainer client, firmer for the project deposit
- For a 2-person business, chasing money is the task owners hate most and delay longest — Quick makes it painless

---

## Act 2: Automate It — Create the Invoice Chaser Agent

*The audience just felt the pain (overdue invoices) and saw Quick solve it (drafted reminders). Now they see it automated permanently. This is the "never think about this again" beat.*

### What to say:

> "Those reminders were great — but Darla shouldn't have to ask for them every time. Let's make sure overdue invoices get caught automatically."

### Demo Action:

**Prompt:** "Create an agent that checks for overdue invoices every Monday and Wednesday, drafts polite reminders adapted to each client relationship, and sends me a summary of what's outstanding."

### Expected Result:

Quick sets up the recurring helper and confirms it:

- Name: "Invoice Chaser" (or similar)
- Checks all outstanding invoices, flags overdue ones
- Drafts reminders with tone adapted per client (warm for retainers, firmer for project deposits)
- Sends Darla a summary of what's outstanding and what was sent

> **What to expect:** Depending on the environment, Quick may set this up as a named **agent** or as a **scheduled task / automation** — either is fine. The point that lands is the same: a recurring helper that now runs on its own without Darla asking. Narrate the outcome ("it now runs on a schedule"), not the specific artifact type.

### Follow-up:

**Prompt:** "Have it run every Monday and Wednesday at 9 AM."

**Expected Result:** Quick confirms the cadence — it now runs automatically twice a week, no further action needed. (If Quick already set the schedule when it created the helper, just confirm the timing.)

### Talking Points:

- Three beats in one story arc: the audience saw the pain (Act 1), saw Quick solve it (Act 1), and now saw it automated permanently (Act 2)
- This is the **build-once, runs-on-a-schedule** concept: set up a reusable helper once and it works in the background
- If the helper shows up in the Agents (or Automations) library, you'll point to it in Act 3 when we talk about sharing with Diego — if your environment files it elsewhere, just narrate that it now runs on a schedule
- For a micro-business, chasing money is the #1 task that slips. This is the kind of automation that pays for the subscription in the first week


### Kick Off the Proposal Now (Run It in the Background):

*The Harbor Point proposal (Act 5A) is the longest single generation in the demo, and it runs in two steps: a fast first pass that produces the outline, then a longer pass that writes the full document. Start it here, right after the automation beat, so it finishes quietly while you present Acts 3 and 4. You will return to the completed proposal in Act 5A for the Knowledge Graph reveal.*

**Return to the Activity Feed** and click the **"Harbor Point B&B — Proposal Needed for Kickoff"** card, then select **"Create proposal"** and send the Act 5A prompt (reproduced there).

**Confirm the fast first step, then walk away:** When the outline appears (this part is quick), point to it as the plan Quick is about to flesh out, then let the full document keep generating in the background. Narrate:

> "I am going to kick off Darla's client proposal now. Quick drafts the outline in a moment, then writes the full thing while we keep moving. A real client deliverable takes a beat, same as it would for a person, so we will let it work and come back to it."

Then move into Act 3. By the time you reach Act 5A, the finished proposal is ready and the reveal lands without a wait.
---

## Act 3: Team Scaling — Agents, Skills & Sharing

*This act shifts from "Darla working alone" to "Darla scaling." A micro-business scales in two directions: AI agents that automate the recurring work, and human teammates who take on the rest. Walk the audience through both. This is a talk track act — crisp and punchy, no live prompt needed.*

### What to show:

Open the **Agents library** (in the left sidebar, **Customize** then **Agents**; **Skills** live under Customize too, right next to Agents). Show:

- **Built-in agents and skills** that come out of the box
- The **Invoice Chaser** you set up in Act 2 — if it appears here (or under Automations), point it out; if not, just narrate that it's now running on its schedule in the background
- Explain: *"Darla just grew her team — not by hiring, but by building a helper. The Invoice Chaser runs on a schedule, handles a task she used to do manually, and never needs to be reminded. That's one kind of scaling."*

### Talk Track — Scaling with Agents AND People:

> "A micro-business scales in two ways. The first is agents — automated helpers that take recurring work off your plate entirely. We just built one: the Invoice Chaser now runs every Monday and Wednesday without Darla thinking about it. She could build more — a weekly digest agent, a content scheduler, a client onboarding agent — each one handling a task that used to eat her time."

> "The second is people. Darla brought Diego onto her Quick Plus account, and now they share a common workspace. Anything Darla builds — an agent, a Space, a scheduled task, an app — she can share with Diego in one click. Diego doesn't need to set anything up; he starts using what Darla built. She could create a 'Client Voice Brief' agent, share it with Diego, and now he matches every client's tone without a briefing from her."

> "The key idea: Quick lets you scale in both directions at once. Agents handle the repetitive work. Teammates handle the creative work. And everything Darla builds — agents, apps, Spaces — is shared across both. The tools compound as the team grows."

### Talking Points:

- **Two scaling levers:** agents automate the recurring work (invoice chasing, digests, onboarding); human teammates handle the creative and relational work (client strategy, content, pitching)
- **Sharing is the multiplier:** everything Darla builds — agents, Spaces, apps, scheduled tasks — is shareable with her team in one click
- **The Invoice Chaser is proof:** set up 2 minutes ago and already running on a schedule as a reusable, shareable tool
- **This is why Quick Plus works for micro-businesses:** you don't need to hire to scale — you build agents first, then add people when you're ready, and everything compounds

---

## Act 4: Client Dashboard App — A Better Way to Work as a Team

*This act shows Quick Apps as a collaborative tool. The dashboard is pre-built before the demo — the presenter walks it and makes a minor edit live. This follows naturally from Act 3's team-scaling talk track: "We just talked about sharing tools with Diego — here's one in action."*

### Setup (before the demo):

Pre-build the Client Dashboard app in a separate conversation:

**Prompt:** "Build me a Client Dashboard app for AnyCompany SOHO Studio. I want two sections: (1) At the top, two charts side by side: a bar chart showing monthly revenue by client, and a donut chart showing invoicing status — what's been paid, what's outstanding, and what's overdue. (2) Below the charts, a client table with all 7 clients showing name, service tier (retainer vs. project), status, next deliverable due, and monthly revenue. Include Bloom & Vine, Foundry Coffee, Cedar & Co. Law, Rivera Family Dental, Northside Yoga, Grid Street Bikes, and Harbor Point B&B (new) — what's been paid, what's outstanding, and what's overdue. Make it visual, clean, and something Diego and I can check every morning."

Save the generated app so it's ready to open during the demo.

### What to show:

Open the pre-built Client Dashboard app. Walk the audience through it:

- **Revenue by client chart** — a bar chart showing monthly revenue per client, making it immediately obvious where the money comes from and how concentrated the business is
- **Invoicing status chart** — a donut showing paid vs. outstanding vs. overdue. Ties directly back to Act 1 — the audience recognizes the overdue slice from the invoice reminders
- **The client table below:** 7 clients at a glance — retainer vs. project, status, what's due next
- **Harbor Point B&B** showing as "New — onboarding" with the kickoff call as the next milestone
- **Grid Street Bikes** flagged with the overdue deposit
- **Cedar & Co. Law** showing the overdue invoice status

### Live Edit:

Make a small update to show the app is interactive:

**Prompt:** "Update the Client Dashboard — move Harbor Point B&B status from 'New' to 'Onboarding' and add the kickoff call as their next deliverable."

### Talk Track:

> "This is an app Darla built with a single prompt. Diego sees the same view — so when they sync on Monday morning, they're both looking at the same picture. No spreadsheet to update, no Slack message asking 'what's the status on Cedar?' — it's all right here."

> "And this isn't just data stored inside Quick. The app connects to the same systems Darla already uses — her invoicing app like QuickBooks, her CRM, her task tracker, her email and calendar. When that Grid Street deposit gets paid in QuickBooks, it reflects here. When a client emails about a deliverable, the status updates. The app is a live window into her real tools, not a copy of them."

> "That's the key idea with Quick Apps: they sit on top of your existing systems and pull everything into one view. As Darla adds more connectors — a CRM, a project tracker, an accounting tool — the apps get richer automatically."

> "And as the team grows, this scales safely. Diego sees the same dashboard, but he only sees the data he's allowed to see. Permissions carry over from the connected systems — so if Darla has access to financials in QuickBooks but Diego doesn't, his view reflects that. You're not rebuilding access controls inside Quick; they come from your existing tools."

### Talking Points:

- The app was built with one prompt — no developer, no extra subscription
- It's a shared team tool: Darla and Diego both see the same client status
- The app is a live window into Darla's real systems — QuickBooks, email, calendar — not a static copy. As she connects more tools, the app gets richer automatically
- This is the bridge between "AI assistant" and "AI-powered tool" — Quick doesn't just answer questions, it builds things you use every day

---

## Act 5: Client-Ready Deliverables — The Knowledge Graph at Work

*This is the strong finish. Quick turns connected context into polished, send-ready output. The Knowledge Graph is the engine — Quick pulls from emails, chat, calendar, and relationships to assemble deliverables no one had to brief it on.*

### 5A: Harbor Point Proposal

**Return to the proposal you kicked off after Act 2.** It has been generating in the background while you presented Acts 3 and 4, so the finished, client-ready document should be waiting. (If you skipped the early kick-off, start it now from the **"Harbor Point B&B — Proposal Needed for Kickoff"** card via the **"Create proposal"** CTA, and note that the outline appears fast while the full document takes a moment longer.)

**Setup:** Sam said yes. Darla needs a professional proposal before the kickoff call. Quick has everything it needs from connected context.

**The prompt that was sent:** "Create a proposal for Harbor Point B&B based on everything we know — Sam's email about the timeline and annual commitment, our services rate sheet, and the capacity assessment. Include a project overview, scope of work, a 6-week timeline, and pricing. Make it polished and client-ready."

### Expected Result:

Quick pulls from the KG (Sam's email, Harbor Point as a new client, the 6-week timeline, the annual discount question, Marcus Lindqvist's rates, the capacity constraints) and generates a complete proposal:

- Project overview referencing Sam's specific goals (launch before busy season, annual commitment)
- Scope: brand identity + campaign launch
- 6-week phased timeline (discovery → design → content → launch)
- Pricing with a retainer option and the annual commitment discount Sam asked about
- Team: Darla on strategy, Marcus Lindqvist on design, Diego on content support

### Talk Track — The KG Reveal:

> "Quick didn't write this from a generic template. It pulled Sam's exact words from the email, the rate sheet from our files, the team availability from the calendar, and Marcus Lindqvist's rates from a completely separate conversation. All of that context lives in what we call the Knowledge Graph — a connected map of everything Quick has learned about your business."

*Optionally flash the ****Knowledge Graph**** view to show the Harbor Point cluster: Sam → Harbor Point B&B → the launch project → Darla → the Services Rate Sheet (with the annual-commitment discount) → Marcus Lindqvist → his rate increase.*

> "That's the difference between a chatbot and a work companion. A chatbot answers one question at a time. Quick connects everything and produces work that reflects your full context."

---

### Talking Points:

- This is the emotional payoff of the demo: Quick produces real, client-ready work — not just answers, but deliverables
- Every fact in the proposal and report was pulled from connected context — email, chat, files, calendar — via the Knowledge Graph
- For a micro-business, proposals and reports are the work that wins and retains clients. Quick does them in seconds instead of hours
- Multiply the Foundry Coffee report by 5 retainer clients and Darla saves an entire day each month

---

## Closing: Take Quick With You

*The day opened with a two-minute connection at Darla's desk. It ends with that same connected experience in her pocket — and one last Quick-in-action moment.*

### The Closing Image (Prepare This Ahead of Time):

*This is a fun closing beat: show an image of the persona on the move, then use it to narrate the mobile story. Create this image BEFORE the demo and have it ready to display, rather than generating it live. Generating ahead avoids a wait during the close, lets you pick the best result from a few tries, and keeps the ending crisp.*

**How to create it beforehand:** In Quick, load the image generation skill and use the prompt below. Note the prompt deliberately does not name the persona, so it works for any market with no edits. Generate a few options and save the one you like best to drop into the close.

**Prompt:**

> Stylized editorial illustration, warm painterly digital art style. A confident young woman walking through lively downtown streets of Austin, Texas on a sunny afternoon. In her right hand a takeaway coffee cup; in her left hand she clearly holds a smartphone raised up, glancing at the screen with a warm confident smile. Smart-casual business attire. Golden hour lighting, warm brick and modern storefronts, tree-lined sidewalks, shallow depth of field, soft background blur. Vibrant magazine-style illustration.

**Negative prompt (things to keep out):**

> photorealistic, photograph, text, watermark, low quality, distorted face, extra fingers, both hands on cup, no phone

> **Localization note:** Adjust the setting to match your market while keeping the same stylized illustration style. Swap the city and streetscape (for example Shibuya in Tokyo, Covent Garden in London, or Jardins in Sao Paulo) and keep the rest of the prompt as-is. Do not name the persona in the prompt.

**Display the image and narrate:**

> "Darla's day started at her desk with a two-minute setup: she connected her email, calendar, and team channel. Since then, Quick has chased her overdue invoices, automated the follow-ups permanently, helped her assess whether she can take on a new client, built a dashboard for her team, written a proposal, and produced a client report. All from that one connection."

**Then bring it home:**

> "And now she's heading out. Quick goes with her: same account, same connections, same memory, right on her phone. She can ask 'Did Bahia Serena reply to the proposal yet?' and get the same answer she'd get at her desk. That's the story: connect once, and it works everywhere."


### Talking Points:

- **Bookend the story:** The day started with a two-minute connection and ends with that same connection in her pocket — this is the emotional payoff
- **No reconfiguration:** Same account, same connected sources, nothing to reconnect on mobile. The morning brief, the proposal, the tracker — all right there
- **For a micro-business owner**, work doesn't stop at the desk. Quick goes wherever the work goes — that's the promise of a connected experience on a single subscription
- **If showing to a telco audience:** This is the telco's core value — connectivity on the go. Quick Plus on mobile is the same subscription and the same experience she set up that morning, now in the carrier's network in her pocket

### Optional — show mobile live:

If you have Quick on a phone, open it after the image generates. Show the same account, same connected data, and ask: *"What's still open for me before tomorrow?"* — proving the work follows her off the desk.

---

## Production Notes

- **Run the day in order** for the full narrative. Each moment builds on the last, and the connect-first / mobile-last bookends are the story arc.
- **Keep everything on Darla's screen.** Show her sharing agents, Spaces, and apps with Diego; narrate that Diego then uses them. Don't switch accounts.
- **Natural phrasing works.** Presenters and audience members won't use exact prompts. Variants like "Catch me up," "Do we have capacity?", "Write the proposal," or "Make this interactive" all produce similar results.
- **Activity Feed is your guide.** Walk the feed cards top to bottom — every demo moment has a clickable CTA. Pick the cards that match your time and audience.
- **For video capture:** Record each prompt being typed, show a brief thinking indicator, then reveal the response with key sections highlighted. 60-90 seconds for quick moments, 2-3 minutes for main scenarios.
- **About this demo environment:** This is a sample workspace for demonstration. The Amazon Quick desktop experience shown here is representative of what users see. Everyone, every company, and all data in the showcase are fictitious.

---

## Appendix: Additional Demo Prompts (Mix & Match)

*These standalone prompts can be used at any point in the demo to show additional capabilities. Each one works against the data already loaded in the environment.*

| Prompt | What It Shows |
| --- | --- |
| "Reply to Dr. Sánchez at Lumen Pediatrics — I'd love to chat. Suggest Thursday morning and keep it warm since Ana referred him." | **Email drafting** with tone and context from KG (referral relationship) |
| "Move the Northside Yoga check-in from Wednesday to Thursday this week and reply to Tess confirming." | **Calendar management** — rescheduling + automated reply |
| "What's my cash position right now? Walk me through what's paid, what's outstanding, and what's overdue." | **Financial synthesis** from invoicing data across multiple clients |
| "Draft a reply to the Chamber speaking invitation — accept it and ask about logistics. This could be good exposure." | **Decision + email drafting** for an opportunity |
| "What should Diego work on this week? Prioritize his tasks based on what's in the feed and what clients need." | **Task delegation** — Quick reasons across all open items and Diego's ramp status |
| "Harbor Point wants to start a brand and campaign and launch in about six weeks. Look at our current client load, the project in delivery, and my calendar. Can we realistically take this on?" | **Strategic capacity assessment** — Quick reasons across client roster, project status, calendar, and subcontractor rates |
| "Summarize everything I know about Marcus Lindqvist — projects, rates, availability." | **KG deep dive** on a specific person with cross-referenced context |
| "Update the Rivera Dental newsletter — swap the second tip to something about summer tooth sensitivity and cold drinks, then show me the updated version." | **Targeted edit** to an existing deliverable — Quick knows which newsletter, which tip, and what the replacement should cover |
| "Build a project tracker for the Grid Street Bikes brand project with milestones, status, owners, and due dates." | **App creation** — turns a mental checklist into a shareable interactive tracker |
| "Draft the Bloom & Vine rush post: an Instagram caption with a call to action and 5-7 hashtags, plus 3 story frames for a same-day wedding order, in Bloom & Vine's voice." | **On-brand content creation** — Quick matches Bloom & Vine's warm, floral tone without being told the brand guidelines |
| "Build a monthly performance recap for Foundry Coffee Roasters — social engagement, newsletter performance, what we shipped this month. Make it something I can send directly to Marcus Bell." | **Client report generation** — Quick assembles a send-ready recap from connected data across multiple channels |

---

