# Workflow Manager

A desktop app (Python, PyQt6, Microsoft Fluent 2 look) that manages the workflows, workflow schemes and statuses of a
**Jira Cloud** site and publishes workflow documentation to **Confluence Cloud**. It can also export the publishing as a
self-contained script that a server can run on a schedule.

- [Requirements](#requirements) · [Permissions and scopes](#permissions-and-scopes) · [Getting started](#getting-started)
- Step by step: [1 Sign in](#1-sign-in) · [2 Workflows](#2-workflows) · [3 Workflow viewer](#3-workflow-viewer) ·
  [4 Schemes](#4-schemes-workflow-schemes) · [5 Statuses](#5-statuses) · [6 Resolutions](#6-resolutions) · [7 Template](#7-template) ·
  [8 Publish](#8-publish) · [9 Job](#9-job-and-the-generated-script) · [10 Logs](#10-logs) · [11 Settings](#11-settings) · [12 Help](#12-help)
- [Troubleshooting](#troubleshooting) · [What's new](#whats-new) · [Keyboard shortcuts](#keyboard-shortcuts) · [Files the app writes](#files-the-app-writes) · [Build](#build-the-executable)

## Requirements

- Windows with the released `WorkflowManager-vX.Y.Z.exe`, **or** Python 3.9+ with `pip install -r requirements.txt`
  (PyQt6, requests) and `python main.py`.
- A Jira Cloud site. To publish: a Confluence Cloud site on the same Atlassian site.

## Permissions and scopes

The app only does what the signed-in account may do. Scopes only *limit* what a service account's credentials may request.

### Account permissions (both sign-in methods)

| Where | Needed for | Permission |
|---|---|---|
| Jira | Reading workflows, schemes, statuses, screens; deleting workflows and schemes | **Administer Jira** (global permission) |
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

Before you sign in, Workflows, Schemes, Statuses, Resolutions and Publish are disabled; Template, Job, Logs and Settings are available.

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

Lists the workflows of the site (read from Jira; refreshed with **Fetch Workflows** or `F5`).

- **Status**: *Active* (used by a workflow scheme) or *Inactive*. **Space** is a drop-down in which you can tick any number of spaces
  (Select All and Clear are at the top; untick a space to remove it; nothing ticked means all spaces) and Jira Service Management
  spaces carry a green **JSM** tag. The search box (workflow or scheme name) sits in the same row, after the options.
- **Columns**: Workflow (link to the Jira editor, never wrapped; a green **JSM** tag marks every workflow that a Jira Service
  Management space uses), Workflow ID, Space, Workflow Scheme (link), Last Updated, Statuses, Properties (shown as `S(5) / T(3)`: the number of status properties and the number of
  transition properties; the tooltip lists them),
  Transitions, Screens, State, and a **View** button. Click a header to sort.
- **Select**: row checkboxes, and the header checkbox for the rows on the page. Changing Status clears the selection.
- **Open**: double-click a row or click **View** for the [workflow viewer](#3-workflow-viewer).
- **Export CSV**: every workflow matching the filters (with separate status-property and transition-property counts).
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
  **Use Jira Status positions** places the statuses where the Jira workflow editor has them (the X / Y values Jira reports, read as the top-left corner of a box that Jira sizes by its text, so statuses that Jira stacks in
  one column share a centre line and their lines run straight, as in the Jira editor; every line also leaves and enters its status at the same
  port (top, corner, side or bottom) that Jira used, and the two directions between two statuses keep their own transitions; when both use the same ports they are drawn on one line with a
  head at each end, as in the Jira editor; the statuses and lines are never moved for the labels: when many transitions share one stretch and a label finds no free
  place on it, the label moves to the nearest free place beside the line and a dotted leader joins it to its line, so labels never overlap
  each other or a status; all labels are horizontal (they are never turned along a line);
  disabled when Jira gave none). The drawing is enlarged just enough that labels and lines have room.
  **Show all transition labels**: otherwise labels show for the hovered or selected item only.
- The first line on a side of a status always leaves or enters at the **centre of that side**, also with *Use Jira Status positions*
  (a line that cannot run straight from centre to centre gets a short bend instead); further lines on the same side sit beside it.
- Labels are small, sit on their own line, are horizontal when there is room and turn along the line when not, and
  never touch other lines, statuses or labels.
- **Save PNG** saves the diagram; **Edit in Jira** opens the workflow editor.
- Tabs **Statuses** (ID, status, category, status properties) and **Transitions** (ID, name with link, From → To, screen with link,
  transition properties). A transition that customers can take in the Jira Service Management portal (property
  `servicedesk.customer.transition.active = true`) carries a yellow **Portal** tag next to its name, and the same tag is drawn at
  the end of its label in the flowchart, the PNG, and the draw.io diagram.
  Selecting in the chart selects the table row and the other way round.

## 4. Schemes (workflow schemes)

- Filters: **Status** (Active / Inactive), **Space** (the same multi-select drop-down as on Workflows), search (scheme, workflow or space).
  **Fetch Schemes** refreshes.
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
- The page is read only: statuses cannot be selected or deleted in this app.

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

Designs the Confluence page. **Save Template** keeps it in `template.json`; **Reset to Defaults** restores it. The **Preview** on the right shows the page for a ticked workflow (choose which one in the list above it) the way Confluence will show it: it
is built from the very page text that is published (layout columns, table of contents, page properties, diagram, tables, code block, labels),
with the **page hierarchy** above it (the parent page or folder, the content properties report, and one page for each workflow with its
title and labels). Drag the handle between the settings and the preview to give the preview more room. The app asks to save unsaved changes when you close it.

- **Selected workflows** and **Choose workflows** (back to Workflows).
- **Page layout**: Columns (Single; Two columns narrow left / narrow right / equal; Three columns equal / narrow sides) and
  **Use the full page width**.
- **Page elements** (tick to include): *Table of Contents macro*, *Workflow Information*, *Workflow Diagram*,
  *Workflow Details* (option: its own full-width section), *Raw JSON code block macro* (options: collapse, line numbers).
  **Placement**: drag elements between the columns or use ↑ ↓ ← →.
- **Workflow diagram**: Width, *Show the transition labels*, *Use Jira Status positions*, **Status Size** (75–150 %), **Line Style**
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

Every change the app makes (delete workflow / scheme, publish page, add report) is appended to
`change_log.jsonl`: time, user, site, action, target, ID, result, details. The page shows them newest first with search,
**Open log file**, **Export CSV** and **Refresh**. Reading data and exporting are not logged. The exported script has its own log.

## 11. Settings

- **Color mode**: Light, Dark or Use system setting. **Accent color**: seven swatches. **Text size**: Small to Extra large.
  **Reset Appearance**. Changes apply immediately and are remembered.
- **Preview** of the controls, and **Data and About** (version, release date, **Open Data Folder**).

## 12. Help

The **Help** page (side bar, above Settings; or press `F1`) is this user guide inside the app. The topics are on the left (one for each
section of this README); the open topic is on the right with **Previous** and **Next** buttons to read on. Pressing `F1` on any page opens
the topic of that page. The search box above the list keeps only the topics that contain every word you type and scrolls to the first
match. **Troubleshooting** takes you straight to the solutions of common problems. Links between topics work inside the page; web links open
in your browser. The guide is the README.md that comes with the program (it is bundled in the executable).

## Troubleshooting

| Problem | What to do |
|---|---|
| *Authentication failed* when you sign in | Check the site URL and the email. The API token must be pasted completely (a missing last character gives this error). |
| *Not allowed to read … (403)* | Reading and deleting workflows, schemes and screens needs the **Administer Jira** permission. |
| A service account is rejected | Create it in the Atlassian organization that owns the site, with the [scopes](#permissions-and-scopes) of the sign-in page (button **View the required permission scopes**). A service account cannot publish to another site. |
| Resolutions, statuses or workflows are missing | Click **Fetch Workflows** (or `F5`) to read the site again; the account needs permission to see them. |
| A workflow cannot be deleted | Only inactive workflows and schemes can be deleted, and Jira refuses default workflows and workflows that a draft uses. |
| **Use Jira Status positions** is disabled | Jira did not report positions for that workflow (older workflows). It keeps the automatic layout. |
| Publishing says a page already exists | A page with the same title in the space is overwritten on purpose; Confluence keeps the earlier version in the page history. |
| *Could not attach …* while publishing | Confluence sometimes refuses a new attachment version; the app retries and then replaces the attachment. Publish again if it still fails. |
| The diagram is missing on the page | Use the PNG format, or keep **Also show a PNG preview image** on with draw.io. |
| Help shows no topics | `README.md` was not found next to the program; put it back in the program folder. |

## What's new

- **2.26** – Workflow diagram with *Use Jira Status positions*: statuses at Jira's positions, lines through Jira's own ports, two-way
  transitions drawn as one double-headed line, horizontal labels that never overlap. Help page with this guide, F1 and search.
- **2.25** – *Use Jira Status positions* moved next to *Show the transition labels*; *Status Properties* column in the workflow pop-up.
- **2.22 – 2.24** – Properties column `S(n) / T(m)`; lines enter the centre of a side; Jira positions read as top-left corners.
- **2.21** – Template preview shows the real page with its page hierarchy; multi-select Space filter with JSM tags; Workflow ID column.
- **2.15 – 2.20** – Resolutions page; Statuses read only; Portal and JSM tags; Fluent 2 pass (grouped navigation, one-row command bar).
- **2.0 – 2.14** – Settings page (color mode, accent, text size), sign-in loading steps, service-account scopes, this README.

## Look and feel

The app follows the Microsoft Fluent 2 design language and uses the **Segoe UI** font.

- **Navigation:** the sidebar groups the pages under *Inventory* (Workflows, Schemes, Statuses, Resolutions), *Documentation*
  (Template, Publish, Job) and *Monitoring* (Logs); Sign In is at the top, Help and Settings at the bottom.
- **Page structure:** every page has the same parts: a title, one line that says what the page is for, a command bar (the filter options,
  the search box and the page actions in one row), the content, and a status line.
- **Type ramp:** page titles 24 px, section headings 16 px, body text 14 px, captions 12 px (all scale with the text size in Settings).
- **Wording:** buttons, titles, headings, tabs and column headers are in Title Case (also when a button's text changes, for example
  *Delete (3)*); empty tables say why they are empty; disabled buttons explain in their tooltip what is missing.
- **Tables:** columns size themselves to their text (also after you change the text size): names and tags are never cut, long text wraps,
  and a horizontal scroll bar appears only when the text needs more room than the window has.

Change colors, accent and text size under [Settings](#11-settings).

## Keyboard shortcuts

| Keys | Action |
|---|---|
| `Ctrl+1` … `Ctrl+9` | Sign In, Workflows, Schemes, Statuses, Resolutions, Template, Publish, Job, Logs |
| `F1` | Help (the topic of the current page) |
| `Ctrl+,` | Settings |
| `Ctrl+F` | Focus the search box of the page |
| `F5` | Fetch again (Workflows, Schemes, Statuses, Resolutions) / reload the log |
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
  and `routing.py` the workflow diagram, `page.py` the Confluence page, `previewhtml.py` the page preview and its hierarchy, `template.py` the page template, `jobscript.py` and
  `runner_template.py` the exported job script, `fluent.py` the Fluent 2 theme).
- Code check: `pip install pylint` then `python -m pylint main.py build.py jira_workflows` (settings in `.pylintrc`; the code scores 10/10).
- The released executable of each version is in `dist/`.

