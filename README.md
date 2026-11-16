# Workflow Manager

A desktop app (Python, PyQt6, Microsoft Fluent 2 look) that manages the workflows, workflow schemes and statuses of a
**Jira Cloud** site and publishes workflow documentation to **Confluence Cloud**. It can also export the publishing as a
self-contained script that a server can run on a schedule.

- [Requirements](#requirements) · [Permissions and scopes](#permissions-and-scopes) · [Getting started](#getting-started)
- Step by step: [1 Sign in](#1-sign-in) · [2 Workflows](#2-workflows) · [3 Workflow viewer](#3-workflow-viewer) ·
  [4 Schemas](#4-schemas-workflow-schemes) · [5 Statuses](#5-statuses) · [6 Resolutions](#6-resolutions) · [7 Template](#7-template) ·
  [8 Publish](#8-publish) · [9 Job](#9-job-and-the-generated-script) · [10 Logs](#10-logs) · [11 Settings](#11-settings)
- [Keyboard shortcuts](#keyboard-shortcuts) · [Files the app writes](#files-the-app-writes) · [Build](#build-the-executable)

## Requirements

- Windows with the released `WorkflowManager-vX.Y.Z.exe`, **or** Python 3.9+ with `pip install -r requirements.txt`
  (PyQt6, requests) and `python main.py`.
- A Jira Cloud site. To publish: a Confluence Cloud site on the same Atlassian site.

## Permissions and scopes

The app only does what the signed-in account may do. Scopes only *limit* what a service account's credentials may request.

### Account permissions (both sign-in methods)

| Where | Needed for | Permission |
|---|---|---|
| Jira | Reading workflows, schemes, statuses, screens; deleting them | **Administer Jira** (global permission) |
| Confluence | Publishing pages | View the parent page/folder and the space; **Add pages**, **Add attachments** and **Add labels** in that space; edit permission on the parent page (only to add the report) |

### OAuth 2.0 scopes of a service account

Create the credentials at admin.atlassian.com → Directory → Service accounts. The sign-in page has a
**View the required permission scopes** button (Service Account tab) that lists them, with a **Copy** button per group.

| Product | Need | Scopes |
|---|---|---|
| Jira | Required | `read:jira-work` `read:jira-user` `manage:jira-configuration` |
| Confluence | To publish | `read:confluence-content.all` `read:confluence-content.summary` `read:confluence-space.summary` `write:confluence-content` `write:confluence-file` `read:confluence-props` `write:confluence-props` |

A service account belongs to one site: it cannot publish to another site's Confluence. The exported job script supports
only the username / API-token method (see [Job](#9-job-and-the-generated-script)).

## Getting started

1. Start the app and sign in ([step 1](#1-sign-in)). The app reads your site and opens **Workflows**.
2. Tick workflows and click **Review Template** to design the Confluence page ([step 7](#7-template)).
3. Open **Publish**, enter the parent page URL and click **Publish to Confluence** ([step 8](#8-publish)).
4. Optional: click **Create Job** to export a script that repeats the publishing on a server ([step 9](#9-job-and-the-generated-script)).

Before you sign in, Workflows, Schemas, Statuses, Resolutions and Publish are disabled; Template, Job, Logs and Settings are available.

## 1. Sign in

1. Enter the **Jira site URL** (for example `https://your-company.atlassian.net`).
2. Choose a tab:
   - **Username / Token** – your email and an Atlassian API token (id.atlassian.com → Security → API tokens).
   - **Service Account** – the Client ID and Client secret of the service account. Click **View the required permission scopes**
     to see what the credentials need.
3. Click **Sign in** (or press Enter). The site, email, client ID and the last tab are remembered; **the token and the client
   secret are never saved**.
4. The *Connected* card shows who you are and **Loading your data** with a progress bar and eight steps:
   Reading spaces → statuses → resolutions → workflow schemes → screens and work types → workflow scheme assignments → workflows →
   Saving a local copy. When the load ends the app opens **Workflows** by itself. If a step fails, the message is shown
   with **Try again**. **View workflows** and **Sign out** are on the card too.

The lower left of the sidebar then shows your name and site; hover it to see **Exit app** and click to close the app.

## 2. Workflows

Lists the workflows of the site (read from Jira; refreshed with **Fetch workflows** or `F5`).

- **Status**: *Active* (used by a workflow scheme) or *Inactive*. **Space** and a search box (workflow or scheme name) narrow the list.
- **Columns**: Workflow (link to the Jira editor), Space, Workflow Scheme (link), Last Updated, Statuses, Properties,
  Transitions, Screens, State, and a **View** button. Click a header to sort.
- **Select**: row checkboxes, and the header checkbox for the rows on the page. Changing Status clears the selection.
- **Open**: double-click a row or click **View** for the [workflow viewer](#3-workflow-viewer).
- **Export CSV**: every workflow matching the filters.
- **Delete**: only for **Inactive** workflows. A confirmation lists the names; it cannot be undone and Jira refuses default
  workflows. Each delete is written to the [log](#10-logs).
- **Paging**: Rows per page (25 / 50 / 100 / 200), Previous / Next.
- **Review Template**: opens the [Template](#7-template) page with the ticked workflows.
- **Create Job**: turns the ticked workflows into a [job](#9-job-and-the-generated-script).

## 3. Workflow viewer

A pop-up with the flowchart of one workflow and its tables.

- **Flowchart**: statuses are coloured by category; transitions are lines with labels `name · ID`. Click a status or a
  transition to lock the view (others dim; both directions of a two-way line are told apart). **Reset view**, `Esc` or
  `Ctrl+0` release it. The mouse wheel zooms; **−**, **+**, **Fit**, **100%** also zoom. Transitions from any status get an *Any* tag.
- **Width**: Default, +25 % … +100 % (more room between statuses). **Line style**: right-angle or curved.
  **Use Jira Status positions** places the statuses where the Jira workflow editor has them (the X / Y values Jira reports;
  disabled when Jira gave none). The drawing is enlarged just enough that labels and lines have room.
  **Show all transition labels**: otherwise labels show for the hovered or selected item only.
- Labels are small, sit on their own line, are horizontal when there is room and turn along the line when not, and
  never touch other lines, statuses or labels.
- **Save PNG** saves the diagram; **Edit in Jira** opens the workflow editor.
- Tabs **Statuses** (ID, status, category, properties) and **Transitions** (ID, name with link, From → To, screen with link).
  Selecting in the chart selects the table row and the other way round.

## 4. Schemas (workflow schemes)

- Filters: **Status** (Active / Inactive), **Space**, search (scheme, workflow or space). **Fetch schemes** refreshes.
- Columns: Workflow Scheme (link), Spaces, Work Types, Workflows (links), State.
- **Export CSV** of the rows shown. **Delete** only for Inactive schemes (confirmation; the workflows they use are not deleted).

## 5. Statuses

- Filters: **Status** (*Active* = used by workflows, *Inactive* = used by none), **Category**, **Managed by**
  (*Company-managed* / *Team-managed*), search by name or ID. **Refresh** reads the site again.
- The list opens sorted by **Status ID**, ascending; the status name is never wrapped.
- Columns: Status, Status ID, Category, Managed By, **Associated Workflows** (count; tooltip lists them) and
  **Associated Spaces** (the number of Jira spaces that use the status through their workflows; a team-managed status also counts
  its own space; tooltip lists the keys). Click a header to sort.
- **Expand a row**: click the arrow (▸) at the left of a status, or double-click the row, to see its associated spaces and
  its associated workflows with the spaces each workflow is used in. Click ▾ to close it again.
- **Tags** next to the name: **Built-in** for the statuses Jira creates in every new site (Open, In Progress, Reopened, Resolved,
  Closed, Building, Build Broken, To Do, In Review, Done – recognised by their fixed IDs) and **JSM** for statuses that Jira Service
  Management created (the names of its project templates, such as Waiting for support, Waiting for customer, Pending, Escalated,
  Canceled, Under review, Completed; a status counts when a Jira Service Management workflow uses it or no workflow does).
- Only statuses with **0** workflows can be ticked. **Delete** asks for confirmation and cannot be undone.

## 6. Resolutions

Every resolution of the site, laid out like the Statuses page.

- Columns: **Resolution** (name with its tags), **Resolution ID**, **Description**, **Associated Workflows** (the number of workflows
  that set the resolution) and **Associated Spaces** (the number of Jira spaces whose workflows set it; tooltips list them).
  The list opens sorted by Resolution ID, ascending; click a header to sort.
- **Tags** next to the name: blue **Built-in** for the resolutions every Jira site starts with (recognised by their fixed IDs:
  1–9 and 10000–10003) and red **Default** for the resolution Jira sets when none is chosen.
- **A workflow uses a resolution** when one of its transitions sets it: a post function *Update issue field → Resolution* with a value,
  or the Jira Service Management portal property `servicedesk.customer.transition.resolution`. A transition that clears the
  resolution (empty value) does not count.
- **Expand a row** with the arrow (▸) or by double-clicking it: the associated spaces and the associated workflows, with the spaces
  of each workflow.
- Filters: **Usage** (*All*, *Used*, *Unused*) and a search box (name, ID or description). **Export CSV** and **Refresh**.
- Reading resolutions needs no extra permission or scope. If Jira returns none, the page says so.

## 7. Template

Designs the Confluence page. **Save template** keeps it in `template.json`; **Reset to defaults** restores it. A live **preview**
mock-up (using the first ticked workflow) shows the result. The app asks to save unsaved changes when you close it.

- **Selected workflows** and **Choose workflows** (back to Workflows).
- **Page layout**: Columns (Single; Two columns narrow left / narrow right / equal; Three columns equal / narrow sides) and
  **Use the full page width**.
- **Page elements** (tick to include): *Table of Contents macro*, *Workflow Information*, *Workflow Diagram* (option: *Use Jira Status positions*),
  *Workflow Details* (option: its own full-width section), *Raw JSON code block macro* (options: collapse, line numbers).
  **Placement**: drag elements between the columns or use ↑ ↓ ← →.
- **Workflow diagram**: Width, *Show the transition labels*, **Status Size** (75–150 %), **Line Style**
  (right-angle / curved), **Color Style** (category colours / monochrome), **Format** (PNG, or draw.io with an optional PNG
  preview), **Image Sharpness** (1× / 2× / 3×). **Save diagram…** writes the first workflow's diagram.
- Generated page:
  - **Workflow Information** (in a Content properties macro): name, ID, scope, spaces, schemes, state, last updated, counts, link.
  - **Workflow Diagram**: attached PNG and/or `.drawio` file.
  - **Workflow Details**: *Statuses* table (with an **Edit Status** link in the ID cell), *Transitions* table (rules, triggers,
    properties, event, **Edit Transition** link; customer-portal transitions highlighted green), *Transition Screens* table
    (screen, tab and fields in one table). Columns are sized to their text.
  - **Raw JSON** of the workflow.

## 8. Publish

Creates one Confluence page per ticked workflow.

1. **Target**: paste the URL of the parent **page or folder** and click **Check**; the app shows the title and space.
2. Options: *Create a content properties report on the parent page* (pages only), and *Add a prefix to the page titles*
   (the example title updates as you type).
3. **Labels**: `jira-workflow` is always added. Add more, or **Load Existing Labels** and pick one.
4. Review the **Workflows to Publish** table (Workflow, Page Title, Status); **Remove From Selection** drops rows.
5. Click **Publish to Confluence** and confirm. **A page with the same title in the space is overwritten** (Confluence keeps
   the previous version in the page history).
6. The app renders and attaches the diagram, creates or updates the page, sets the width, adds the labels, then adds the
   *Content properties report* (CQL `label = "jira-workflow" and parent = <id>`) to the parent unless it is already there.
   Each row shows its status; double-click a status to open the page. A summary dialog lists every page and the result.

**Create Job** on this page builds a [job](#9-job-and-the-generated-script) from the same selection and template.

## 9. Job and the generated script

Exports a **self-contained Python script** (Python 3.9+ and `requests`; no Qt) that publishes the same pages without a window,
for example every night. The job stores the workflows and spaces to publish, the template and the destination
(`job.json`); each run reads the *current* data from Jira.

Tabs: **Overview** (what the job does, diagram placeholder option), **Workflows** (selected workflows; spaces whose
workflows are all published), **Template**, **Destination**, **Script** (script name and contact info for the header). A
live **Script Preview** with **Copy** is on the right. **Export Job…** saves `publish_workflows_<parent>.py`.

The script never draws a diagram: a page that already has one keeps it; otherwise a placeholder panel is added (if enabled).

```
python publish_workflows_<name>.py -user you@company.com -token <API token> [--dry-run] [--log-file <path>]
python publish_workflows_<name>.py -viewlog [last|all]
```

- `--dry-run` reads Jira and builds the pages but changes nothing in Confluence.
- The log (default `<script>.log`, rotating) never contains the token. `-viewlog` needs no credentials.
- Exit codes: `0` all published · `1` at least one page failed or a listed workflow/space is gone · `2` the job could not
  start · `130` interrupted.
- Schedule it with cron or the Windows Task Scheduler. A command line is visible to other users of the server, so run it from a
  protected task. The account needs the [permissions](#account-permissions-both-sign-in-methods) above.

## 10. Logs

Every change the app makes (delete workflow / scheme / status, publish page, add report) is appended to
`change_log.jsonl`: time, user, site, action, target, ID, result, details. The page shows them newest first with search,
**Open log file**, **Export CSV** and **Refresh**. Reading data and exporting are not logged. The exported script has its own log.

## 11. Settings

- **Color mode**: Light, Dark or Use system setting. **Accent color**: seven swatches. **Text size**: Small to Extra large.
  **Reset Appearance**. Changes apply immediately and are remembered.
- **Preview** of the controls, and **Data and About** (version, release date, **Open Data Folder**).

## Look and feel

The app follows the Microsoft Fluent 2 design language: buttons, page titles, headings, tabs and column headers are in Title Case
(also when a button's text changes, for example *Delete (3)*), every data page starts with a one-line description of what it is for,
empty tables say why they are empty and what to do, and disabled buttons explain in their tooltip what is missing.
Table columns size themselves to their text (also after you change the text size): names and tags are never cut, long text wraps,
and a horizontal scroll bar appears only when the text needs more room than the window has.
Change colors, accent and text size under [Settings](#11-settings).

## Keyboard shortcuts

| Keys | Action |
|---|---|
| `Ctrl+1` … `Ctrl+9` | Sign In, Workflows, Schemas, Statuses, Resolutions, Template, Publish, Job, Logs |
| `Ctrl+,` | Settings |
| `Ctrl+F` | Focus the search box of the page |
| `F5` | Fetch again (Workflows, Schemas, Statuses, Resolutions) / reload the log |
| `Esc` / `Ctrl+0` | Release the selection in the viewer (`Esc` again closes it) |

## Files the app writes

All under `~/JiraWorkflows/`:

| File | Content |
|---|---|
| `<site host>/*.json` | raw Jira responses of the last fetch (projects, statuses, resolutions, schemes, assignments, workflows, screens, work types) |
| `template.json` | the page template, publish target, title prefix and labels |
| `job.json` | the current job |
| `settings.json` | appearance |
| `change_log.jsonl` | the change log |

Window position and the sign-in site, email, client ID and method are stored in the Windows registry (QSettings,
`Nagarro / Workflow Manager`).

## Build the executable

```
pip install -r requirements.txt pyinstaller
python build.py
```

The result is `dist\WorkflowManager-v<version>.exe`. The version is in `jira_workflows/__init__.py`.

## Development

- Structure: `main.py` starts the app; the code is in the `jira_workflows` package (`ui.py` pages and dialogs, `api.py` Jira client,
  `confluence.py` Confluence client, `model.py` normalised workflows, statuses, resolutions and schemes, `flowchart.py`, `layout.py`
  and `routing.py` the workflow diagram, `page.py` the Confluence page, `template.py` the page template, `jobscript.py` and
  `runner_template.py` the exported job script, `fluent.py` the Fluent 2 theme).
- Code check: `pip install pylint` then `python -m pylint main.py build.py jira_workflows` (settings in `.pylintrc`; the code scores 10/10).
- The released executable of each version is in `dist/`.

