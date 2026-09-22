# Document Generation — User Guide

Generate professional documents directly from Amazon Quick chat.
Type what you need, and get a real downloadable file in minutes.

## Supported Formats

| Format | Best For | Examples |
|--------|----------|----------|
| **PPTX** | Presentations, pitch decks, training slides | Quarterly reviews, strategy decks, onboarding slides |
| **DOCX** | Reports, proposals, memos | Technical designs, SOWs, onboarding guides |
| **PDF** | Invoices, compliance, contracts | Audit reports, certificates, formal proposals |
| **XLSX** | Spreadsheets with formulas and charts | Budget trackers, KPI dashboards, sales pipelines |
| **HTML** | Web pages, dashboards, prototypes | Landing pages, email templates, admin dashboards |

## How to Use

### Step 1: Ask in Chat

Open Quick and type your request in the chat. Be specific about what you want.

**Example:**

> Create a 10-slide PowerPoint presentation about our Q4 business review.
> Include revenue by region, year-over-year growth, key wins, challenges,
> and next quarter priorities.

### Step 2: Review and Submit

Quick will show an **Action review** popup with the tool name
`create_document` and the parameters it detected (document type, content).

Click **Submit** to start generating.

### Step 3: Wait for Generation

The document typically takes **3-5 minutes** to generate. Quick will
automatically check the status and notify you when it's ready.

For complex documents (15+ slides, detailed tables, multiple charts), it may
take up to 8 minutes.

### Step 4: Download

When the document is ready, a **download link** appears in the chat. Click
it to download. The link is valid for **7 days**.

## Example Prompts

Good prompts are specific. The more detail you give, the better the output.

### Presentations (PPTX)

**Basic:**
> Create a 10-slide PowerPoint about AI in healthcare.

**Better:**
> Create a 12-slide PowerPoint presentation for our board meeting about
> AI adoption strategy. Include: title slide, executive summary, market
> analysis with competitor comparison table, 3 slides on our AI initiatives
> with timeline, ROI projections with a chart, risk assessment matrix,
> implementation roadmap by quarter, team structure, budget breakdown, and
> a next steps slide with specific action items and owners.

### Documents (DOCX)

**Basic:**
> Write a project proposal document.

**Better:**
> Write a 10-page Word document for a cloud migration proposal for a
> 500-person retail company. Include an executive summary, current state
> assessment with a table of existing systems, proposed architecture with
> component descriptions, migration phases (3 phases over 18 months),
> risk matrix with likelihood and impact ratings, cost analysis comparing
> on-premise vs cloud over 5 years, and a recommendation section.

### Spreadsheets (XLSX)

**Basic:**
> Create a budget spreadsheet.

**Better:**
> Build a project budget tracker spreadsheet with these sheets:
>
> 1. Summary dashboard with total budget, spent, remaining, and a pie chart
> 2. Monthly expenses (Jan-Dec) with categories: Personnel, Software,
>    Infrastructure, Travel, Training. Include SUM formulas per row and column.
> 3. Forecast vs Actual with variance formulas and conditional formatting
>    (red for over budget, green for under).
> Add a bar chart comparing forecast vs actual by month.

### PDFs (PDF)

**Basic:**
> Generate an invoice PDF.

**Better:**
> Create a professional consulting invoice PDF for Acme Corp.
> Invoice #2024-0847, dated today. From: TechConsult LLC, 123 Main St.
> To: Acme Corp, 456 Oak Ave. Line items: Strategy Workshop (40 hours
> at $250/hr), Technical Assessment (20 hours at $300/hr), Documentation
> (10 hours at $200/hr). Include subtotal, 8.5% tax, and total. Payment
> terms: Net 30. Add a "Thank you for your business" footer.

### Web Pages (HTML)

**Basic:**
> Create a landing page.

**Better:**
> Design a modern SaaS product landing page for "DataFlow" — a real-time
> analytics platform. Include: hero section with tagline and CTA button,
> 3 feature cards with icons, a "How it works" section with 4 steps,
> customer logos bar, pricing table with 3 tiers (Starter $29, Pro $99,
> Enterprise custom), FAQ accordion, and footer with links. Use a dark
> blue and white color scheme. Make it responsive.

## Tips for Best Results

### Be Specific About Structure

Instead of "create a presentation," say exactly how many slides and what
each slide should cover. For spreadsheets, describe the sheets, columns,
and what formulas you need.

### Mention the Format Explicitly

Include the file type in your request to avoid ambiguity:

- "Create a **PowerPoint** presentation..."
- "Write a **Word document**..."
- "Build an **Excel spreadsheet**..."
- "Generate a **PDF** report..."
- "Design an **HTML** landing page..."

### Include Data Examples

For spreadsheets and tables, giving sample data helps:
> Create an employee tracker with columns: Name, Department, Q1 Score,
> Q2 Score, Q3 Score, Q4 Score, Average (formula). Add 10 sample employees
> across Engineering, Sales, and Marketing departments.

### Request Specific Formatting

The agent can handle formatting requests:

- "Use a **blue and white** color scheme"
- "Add **page numbers** and a header with the company name"
- "Include **conditional formatting** — red for values below target"
- "Add **speaker notes** to each slide"
- "Make the table **zebra-striped** for readability"

### Keep Complex Requests in One Message

Don't split your request across multiple messages. Put everything in one
detailed prompt. The agent works best when it can see the full picture
before starting.

## Checking Job Status

If the chat session times out or you close the browser before the
download link appears, you can check status using the **job ID** that
was shown when you submitted the request.

Type in chat:
> Check status [your-job-id]

The assistant will look up the job and give you the download link if
the document is ready.

## Limitations

- **Generation time**: Documents take 3-8 minutes. This is not instant.
- **No images**: The agent generates text, tables, charts, and formatting
  but cannot insert photos or external images.
- **Charts from generated data**: Charts are based on sample or
  described data — the agent cannot connect to your live databases.
- **File size**: Typical output is 50KB-500KB. Very complex documents
  with many charts may be larger.
- **One document per request**: Each chat message generates one file.
  For multiple files, send separate requests.
- **Download link expiry**: Links are valid for 7 days. After that,
  you'll need to regenerate the document.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Action review" doesn't appear | Make sure Document Generation MCP is enabled in your workspace. Ask your admin. |
| Document is still generating after 10 minutes | The agent may have encountered an error. Try again with a simpler prompt. |
| Download link doesn't work | The link may have expired (7-day limit). Ask the assistant to regenerate. |
| Output doesn't match expectations | Add more detail to your prompt. Specify sections, formatting, and data explicitly. |
| "Internal error" message | The service may be temporarily unavailable. Wait a minute and try again. |
