# AWS Quick usage datasets: setup

This guide sets up usage analytics for AWS Quick in your own Quick account. When you're done,
you will be able to view dashboards and chat with Quick in natural language about your
organization's Quick usage.

To set it up, run the `quick_usage_datasets_setup.py` script.

---

## Before you start

You need all of these:

- An **AWS Quick Enterprise** (or Professional) subscription. Note its region, e.g. `us-east-1`.
- Sign-in to **Quick as an admin** (role Admin or Admin Pro) — two steps are done in the Quick console.
- Sign-in to the **AWS console** for the same account, with permission to create S3 buckets,
  CloudWatch Logs deliveries, Athena and Glue resources, IAM policies, and Quick resources.
- Your AWS console set to the **same region as Quick**.

---

## Run the script in CloudShell

1. **Open CloudShell** in your AWS console.

2. Make sure you are in the same region where you provisioned your Quick account.

3. **Upload the script.** In CloudShell, choose **Actions > Upload file** and pick the script.

4. **Run the script in CloudShell.**

   ```bash
   python3 quick_usage_datasets_setup.py
   ```

5. **Read, review, and confirm the resources that the script is about to create.**

6. **The script will redirect you to the Quick admin console for one action.**
   When the script reaches it, it prints a link and waits:
   - **Deny default access** (recommended): stops every Quick author from reading the usage
     logs.

   Then return to CloudShell and type the phrase shown. You don't need to grant Quick access
   to the bucket in the console; the datasets read the logs with Athena through their own
   read-only IAM role.

**If a run stops or CloudShell disconnects, just run the script again** — finished steps are
detected and skipped, so re-running is always safe.

---

## Viewing the datasets

1. Use Quick for a few minutes (some chats, a thumbs up/down).
   - Logging starts only after setup completes, so datasets begin empty.
2. Wait 10–15 minutes.
3. In Quick, open **Datasets**, open each `quick-usage-*` dataset, and click **Refresh now**.
   - Without this manual refresh, each dataset updates on its own daily schedule (a full refresh at 02:00 UTC that reloads the dataset from all of its logs).
4. Open a chat and ask it a question about the dataset.
5. Open the **AWS Quick usage** dashboard in the **Dashboards** section.

A dataset with no rows usually just means nobody has done that kind of activity yet.

---

## Faster refreshes with incremental refresh (optional)

Every dataset includes an **`event_datetime`** column — a real timestamp parsed from each usage
log. This is exactly what Quick's **incremental refresh** needs, so you can turn it on yourself
whenever you'd like refreshes to reload only recent activity instead of the full history:

1. In Quick, open the dataset, choose **Refresh** > **Add/Edit incremental refresh**.
2. Pick **`event_datetime`** as the look-back column and choose a window (for example, the last
   3 days).
3. Save, then let the daily schedule run — or click **Refresh now**.

Incremental refresh keeps each run fast as your logs grow, since Quick re-ingests only the rows
inside your window. You stay in control of the window and can adjust or turn it off at any time;
the daily full refresh set up by the script keeps everything accurate in the meantime.

---

## Troubleshooting

| You see | What to do |
| --- | --- |
| The script seems slow at "Looking for Quick admins" | You typed SEARCH, so it reads every Quick user to find the admins and prints a running count. To skip it, type the admin's Quick user name at the question instead, or pass `--quick-admin-arn`. |
| A dataset shows no rows | No logs of that kind have arrived yet. The dataset refreshes to 0 rows until then (this is normal, not an error). Use Quick, wait a few minutes, and refresh again. |
| A `"wrong identity region"` message | Your Quick account was created in a different region. Re-run with `--region` set to that region. |
| `AccessDenied` on an AWS call | Your console role is missing a permission. Use a role with administrator access, or ask your AWS admin. |
| Bucket `"exists but isn't owned by account ..."` | S3 bucket names are global and this one is taken. Re-run with `--bucket <another name>`. |
| The account ID won't match even though it looks right | Type the digits by hand instead of pasting — copy-paste can add invisible characters. |
| It still won't work | Re-run with `--dry-run` to review the plan, and keep the `quick-usage-setup-<run id>.jsonl` log the script writes — it records every step. |

---

## Appendix A: Kiro user activity datasets (optional)

If your users also use **Kiro**, a second script, `kiro_usage_datasets_setup.py`, adds Kiro's
per-user activity reports to Quick. It creates Kiro datasets, joins them with the Quick usage
datasets above on `user_id`, and builds an **AWS Quick and Kiro usage** dashboard. It works the
same way as the main script: each step shows its plan and waits for a confirmation phrase,
re-running is safe, and `--destroy` removes what it created.

Kiro writes one CSV report per user and day to an S3 bucket you own (see
[Kiro user activity reports](https://kiro.dev/docs/enterprise/monitor-and-track/user-activity/)).
The script reads those reports in place with Athena, just like the Quick usage logs.

### Before you start (Kiro)

- **Finish the main setup above first.** The Quick + Kiro dataset and the dashboard read the
  Quick usage Glue tables and `quick-usage-*` datasets, so the script checks for them in its
  first step and stops, changing nothing, if they're missing.
- A **Kiro profile** in this AWS account, and sign-in to the **Kiro console** with the
  [Kiro administrator permissions](https://kiro.dev/docs/enterprise/iam/). One step is done there.
- Your AWS console and CloudShell set to the **region of your Kiro profile**. Kiro only writes to
  a bucket in that region and in this account.

### Run the Kiro script in CloudShell

1. **Upload** `kiro_usage_datasets_setup.py` (**Actions > Upload file**) and run it:

   ```bash
   python3 kiro_usage_datasets_setup.py
   ```

2. **Tell the script where the Kiro reports go.** It asks whether Kiro already sends user
   activity reports to S3:
   - **Already set up?** Open the Kiro console, choose **Settings**, and copy the S3 URI under
     **Kiro user activity reports** (for example `s3://amzn-s3-demo-bucket/user-activity-reports/`).
     Paste it at the question, or pass it as `--reports-s3-uri`. The script uses the bucket as
     it is and reads the reports already there.
   - **Not set up yet?** Type `NEW`. The script creates a bucket
     `kiro-usage-<account>-<region code>` (public access blocked, SSE-S3 encryption, TLS-only
     bucket policy) with the
     prefix `kiro-user-activity-reports/`.

3. **Step 2: the bucket.** For a new bucket, the script creates it. Either way, it checks that the
   bucket policy lets Kiro (`q.amazonaws.com`) write under the prefix, and adds the statement
   from the Kiro documentation if it's missing. A bucket the script created also gets a statement
   that refuses requests made without TLS. Other statements are kept.

4. **Step 3: turn on the reports (manual step in the Kiro console).** Skipped if reports are
   already in the bucket. Otherwise the script prints a link and waits:
   1. In the Kiro console, choose **Settings**.
   2. Under **Kiro user activity reports**, choose **Edit**.
   3. Turn on **Collect granular metrics per user**.
   4. Enter the S3 URI the script shows, exactly as shown, and save.

   Then type `I TURNED ON KIRO REPORTS` (or `IT WAS ALREADY ON`). Kiro writes the first report
   at the next **02:00 UTC**, then one per day. Earlier days aren't backfilled.

5. **Steps 4 to 7** create the Athena and Glue resources, the Quick datasets, an optional daily
   refresh (03:00 UTC, an hour after Kiro writes) and the optional starter dashboard. Confirm each
   with the phrase shown.

### What you get

| Dataset | What one row means |
| --- | --- |
| `kiro-usage-user_report` | One user, one day, one Kiro client (IDE, CLI or plugin): credits used, overage, messages, conversations, subscription tier, email. |
| `kiro-usage-model_messages` | Messages per user, day, client and model (Kiro adds one column per model to its reports; this dataset turns them into rows). |
| `kiro-usage-by_user_analytic` | The legacy Kiro report: chat, inline suggestions, code review and `/dev`, `/doc`, `/test`, `/transform` use per user and day. |
| `kiro-usage-quick_kiro_by_user` | One user and month, with their Quick use (messages, agent hours) and Kiro use (credits, messages), joined on `user_id`. |

Every column has a short description in Quick (open the dataset to see it), which Quick's
natural-language features use when you ask questions about the data.

The dashboard, **AWS Quick and Kiro usage**, has the tabs **Overview**, **Kiro**,
**Quick + Kiro by user** and **Monthly report**.

Notes:

- Quick and Kiro users are matched on `user_id`, the IAM Identity Center principal id that
  both record (Kiro calls it `UserId`). People in a different identity store than Kiro's don't
  match, and show up as two rows (one Quick, one Kiro).
- The join runs in Athena over the logs in S3, and SPICE stores only one row per user and month,
  so the size of the Quick usage logs doesn't limit it.
- The reports contain no prompts or code, but they do contain each user's email address.

### Removing it

```bash
python3 kiro_usage_datasets_setup.py --destroy
```

It removes the dashboard, schedules, datasets, Athena and Glue resources and the data source role.
It only deletes the bucket (with its reports) if the script created it and nothing else is in it.
For a bucket you set up yourself, the reports and the bucket policy are kept. If you turned the
reports on during setup, it asks you to turn them off in the Kiro console first.

---

## Appendix B: Organization view (optional)

After the setup is done, you can add an org view with a second script, `quick_usage_org.py`. It
runs on its own: upload just that file and run it.

### Before you start (organization view)

- **Finish the main setup above first.** The org view joins the `quick-usage-*` datasets.
- **Quick must sign in through IAM Identity Center.**
- **Your IAM Identity Center users must have their organizational fields filled in.** The org
  view joins each usage record to its user on these fields; a field that's empty in Identity
  Center is empty in the org view too. The fields used are:
  - **Job title** (`Title`) and **User type** (`UserType`)
  - **Cost center**, **Organization**, **Division** and **Department** (the enterprise
    attributes `costCenter`, `organization`, `division`, `department`)
  - **Manager** (`manager`), shown as the manager's user name

  The org dashboard groups usage by **Division**, **Department** and **Cost center**.

### Run the organization script in CloudShell

```bash
python3 quick_usage_org.py
```

It works out the same settings as the setup script and asks you to confirm. Then it creates:

| Dataset | What it holds | Who gets it |
| --- | --- | --- |
| `quick-org-identity-center-users` | One row per IAM Identity Center user, with every field Identity Center has: names, email, phone, address, job fields (title, department, cost center, manager, ...) and preferences. **This is personal data (PII).** | Only the dataset owner |
| `quick-usage-<dataset>-with-org` (one per usage dataset) | Every usage record, LEFT-joined to its user. It adds only the job fields, prefixed `org_`: `user_name`, `quick_user_arn` (the join key), `title`, `user_type`, `cost_center`, `organization`, `division`, `department`, `manager_user_name` and `snapshot_time`. | Only the dataset owner |

It also creates the **AWS Quick usage by organization** dashboard over the joined datasets.
Every column gets a description, which Quick's natural-language features use.

The script stores PII in your account: the org dataset keeps it in SPICE, and the joined
datasets keep each user's job fields. To load the org dataset, it writes a CSV of the users to
`s3://<bucket>/quick-org-dataset/users.csv` (encrypted) and deletes it as soon as SPICE has
loaded it. Share these datasets and the org dashboard with care: everyone you share them with
sees every column for every user.

**The org data does not refresh on its own**, unlike the usage datasets. The joined datasets
refresh daily, an hour after their usage dataset, but that only reloads usage records: the
`org_` fields stay as they were at the last run, and users added since then show no org data.
**You must re-run the script to refresh the org data.** Re-run it with the same command; there
are no separate options for a refresh run. It asks you to confirm each time.

`python3 quick_usage_org.py --destroy` removes only what it created. Run that before destroying
the setup, because the joined datasets read the usage datasets.

---

## Appendix: AWS permissions

The AWS services each script calls:

| Service | `quick_usage_datasets_setup.py` | `kiro_usage_datasets_setup.py` | `quick_usage_org.py` |
| --- | --- | --- | --- |
| AWS Quick (`quicksight`) | ✓ | ✓ | ✓ |
| Amazon S3 | ✓ | ✓ | ✓ |
| AWS Glue | ✓ | ✓ | ✓ |
| Amazon Athena | ✓ | ✓ | ✓ |
| AWS IAM | ✓ | ✓ | ✓ |
| AWS STS | ✓ | ✓ | ✓ |
| Amazon CloudWatch Logs | ✓ | | ✓ |
| IAM Identity Center (`sso-admin`, `identitystore`) | | | ✓ |

A role with administrator access covers all of these.
