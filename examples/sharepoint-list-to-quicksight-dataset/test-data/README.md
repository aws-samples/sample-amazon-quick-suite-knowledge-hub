# Test Data: IT Help Desk Ticket Tracker

## Use Case

A mid-size company (AnyCompany Ltd.) tracks IT support tickets in a SharePoint list.
The team wants to export this data to Amazon Quick Sight to build dashboards that answer:

- **Volume trends** — How many tickets are opened/closed per week?
- **SLA compliance** — What percentage of tickets are resolved within the target SLA?
- **Category breakdown** — Which issue categories generate the most tickets?
- **Agent performance** — How are tickets distributed across support agents?
- **Priority distribution** — Are high-priority tickets being resolved faster?
- **Backlog health** — How many tickets are currently open and aging?

## SharePoint List Schema

| Column | Type | Description |
|---|---|---|
| Title | Single line of text | Short summary of the issue |
| TicketId | Single line of text | Unique ticket identifier (HD-XXXX) |
| Category | Choice | Hardware, Software, Network, Access & Permissions, Email, Other |
| Priority | Choice | Critical, High, Medium, Low |
| Status | Choice | New, In Progress, Waiting on User, Resolved, Closed |
| AssignedTo | Single line of text | Support agent name |
| Requester | Single line of text | Employee who submitted the ticket |
| Department | Choice | Engineering, Sales, Marketing, Finance, HR, Operations, Legal |
| DateSubmitted | Date | When the ticket was created |
| DateResolved | Date | When the ticket was resolved (blank if open) |
| SlaTargetHours | Number | SLA target in business hours |
| ActualResolutionHours | Number | Actual hours to resolve (blank if open) |
| SlaMetYesNo | Yes/No | Whether the SLA target was met |
| Notes | Multiple lines of text | Resolution notes or current status |

## Quick Sight Dashboard Ideas

1. **Ticket Volume Over Time** — Line chart of tickets opened per week, colored by priority
2. **Category Pie Chart** — Breakdown of tickets by category
3. **SLA Compliance Gauge** — Percentage of tickets meeting SLA
4. **Agent Workload Bar Chart** — Tickets per agent, stacked by status
5. **Average Resolution Time by Category** — Bar chart comparing categories
6. **Open Ticket Aging Table** — List of open tickets sorted by age
7. **Department Demand Heatmap** — Tickets by department and category

## File

- `helpdesk-tickets.csv` — 1,000 sample tickets spanning 2025-01-06 through 2025-03-28
