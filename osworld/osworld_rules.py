# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""How OSWorld 1.0 judges a task: the one statement of its rules for every prompt.

The cua agent's v1 addendum and the gate both use V1_RULES, so feasibility and
"what counts as done" are defined once instead of in scattered examples across
prompts and skills (gimp 2e6f678f and calc 30e3e107 each followed one of two
conflicting statements). They name no task.

Rule 1 (the machine's state is graded, not the transcript) is how OSWorld
works. Rule 2 follows the paper's own definition (arXiv 2404.07972, sec. 3.2):
infeasible tasks are "inherently impossible to be completed due to deprecated
features or hallucinated features raised by real users" -- a user asking an
application for a feature it lacks -- and sec. 5.4 / app. D.3, where an agent
that cut a video with ffmpeg for "use GIMP to cut ..." is "Done, but not follow
the demanding in instruction". Reading a request for a capability as a request
for that capability (not only its end state) is our reading of those two. It
matched the labels of the 8 tasks we checked (gimp 2e6f678f, 5ca86c6f,
38f48d40, 045bf3ff, fbb548ca; vs_code 7c4cc09e; thunderbird a1af9f1c; a calc
fill task), most of them dev tasks, so it is not yet validated on unseen ones.
Revisit it when a failure suggests it refuses feasible tasks. (gimp dbbf4b99 and
vs_code 7aeae0e2 were infeasible in OSWorld 1.0 but are feasible in Verified, so
they are not evidence for rule 2.) A task that names several applications and
is graded by its result file is feasible (multi_apps: 100 of 101 tasks), so
"combine other programs" in rule 2 applies only when ONE application is asked
for a capability it lacks.
"""

V1_RULES = """\
## How OSWorld 1.0 judges a task

These are the rules of this environment. They hold for every task.

1. What is judged. The result is read from the computer's state after you stop: the files on disk and the open
   application's own state and settings, not your summary. The screen shows that state, but a change counts only
   once it is saved where it is kept (the file on disk, the application's settings). Every task ends one of two
   ways: its end state reached and saved, or declare_infeasible.

2. An end state, or a capability. Some tasks ask for an end state ("change this value", "put these items in
   order"): reach it in any reliable way within the named application. Others ask the named application for a
   particular capability -- a mode, a command, a format, a way of working ("all files in one run", "inside <the app>",
   "with nothing but <the app>'s own features", "save it as <format> from <the app>"). Then that capability is itself \
what is asked.
   Check whether the application offers it to its users: its menus, dialogs, settings and file formats, or an
   extension from its own extension manager (unless the task rules extensions out). Hard to find is not missing:
   try the application's own way once -- open the file, look in the menu or dialog -- before you decide. If you
   would have to build the capability yourself -- write a script or macro that imitates it, hand-edit its files
   to add what its settings do not offer, supply an input the application cannot get on its own, or, when the task \
asks that one application for the capability, bring in another program to
   do it -- the application does not have it, and the task is infeasible: the user asked for that capability,
   not only for its end state. OSWorld's infeasible tasks are of this kind: real users asking an application for
   a feature it does not have, or no longer has. A task that names several applications and is judged by its
   result file is not such a request: do each step in the application that does it.

3. Other infeasible tasks, and what to do. A task is also infeasible when a file, device or account it needs does
   not exist and cannot be obtained, or when its own constraints ("only with", "without") rule out every way.
   Before you act, check that what the task refers to exists on this machine, with one command each: a name
   written as a shell variable or placeholder (`echo "$name"`, then the environment and the shell's startup
   files), a user or group (`getent passwd NAME`), a file or folder (`ls`), a device a setting reports on (
   `ls /sys/class/<subsystem>`, `lshw -short`). A reference that resolves to nothing, or to something the task never
   named, makes the task infeasible; do not guess which similar thing was meant. A device counts as missing only
   when the thing the setting acts on does not exist: if the software object the task changes exists (an audio
   output sink, a display, a network connection), the task is feasible without physical hardware behind it, and
   a task whose requested state already holds is feasible. A task whose requirements
   cannot both hold (two stated limits that exclude each other, a feature "without" the only means that
   gives it) is also infeasible. A setting the application ignores, or a flag it does not list, achieves nothing. For \
any infeasible task, call
   declare_infeasible and change nothing; do not take a substitute route to make the result look done. Saying it
   cannot be done only in your summary does not count.

4. How to do a feasible task. Make what the task names -- an object, a setting, a mode or view -- with the
   application's own command or dialog for it, and keep that command's defaults. Do not assemble a look-alike by
   hand, and do not create an object (pivot table, chart, layout, master) through the application's scripting API
   or by editing its files: those differ in details the user never sees. Do not use the application's macros or
   scripting (LibreOffice's Tools > Macros and Basic IDE, GIMP's Script-Fu), also not to apply one change to many
   items: work through its menus, dialogs and keyboard shortcuts. A setting the application offers may be written in \
its own settings file
   when that file is where the application documents it (VS Code's settings.json), not where the application rewrites \
it from memory
   (Chrome's Preferences). Plain content (cell values, a text file) may be produced
   whichever way is reliable, a script on the file included. Content copied or extracted from a source is compared
   with a verbatim copy: keep every source line unchanged, also lines the target format rejects (a notebook's `!`
   or `%` lines). Text in a document or slide is typed through the
   application, which stores the font name and size on each run, as the comparison reads them. When the application's \
menus offer a conversion
   or export (VLC's Convert / Save, LibreOffice's Export or Save As), its own command line for
   that conversion -- `cvlc`, `soffice --headless --convert-to` -- counts as using the
   application: prefer it for conversions. It never adds an operation the menus lack (rule 2).

5. Leave the application on the result. The application the task is about must end open on the saved result. If
   you changed its file from outside, reopen or revert it there so the open window shows your result, and
   leave it open: the result may be exported or saved again from that window. When you stop, that window is in
   front with no dialog or menu open: the grader presses its keys in whichever window has the focus.

6. Check it as the user would. Compare the result with the original: the change is visible and proportionate,
   nothing destroyed or clipped, nothing changed that the task did not ask for. Read loose words against the screen
   ("the second image" is counted in the order a viewer reads the screen; a move with a direction but no
   distance goes to that edge).
"""

# Rules that held across apps in the 2026-10-09 trajectory comparison with the
# Opus 5 reference, kept here rather than in skills: only 30/70 exp2 runs read any
# skill (multi_apps 9/40, vlc 0/2), so a rule in a skill often never reaches the
# agent. The strings name no task; where each sentence came from:
# - vlc 59f21cfb: launched a second VLC; the grader reads the instance setup opened.
# - multi_apps ee9a3c83/2b9493d7/3680a5ee: ran the commands in hidden bash; graders
#   read the open terminal's history / TTY.
# - impress f23acfd2 (4/4 runs) invented a sentence instead of bulleting; multi_apps
#   2c1ebcd7 rewrote an already-correct reference (0 in 8/8 runs).
# - writer 88fe4b2d dropped the spaces twice when splitting paragraphs.
# - impress e4ef0baf: chose "the picture" from the file structure (an off-slide
#   background) instead of the visible photo.
# - impress 550ce7e7: the file opened on slide 5's to-do list; slide 2 also had a list.
# - multi_apps 185f29bd: a PDF form is read through its field values.
# - multi_apps 02ce9a50: the check reads the terminal screen; `clear` removed the output.
# - Save location: multi_apps 8df7e444 (the zip belongs next to the packed paper, not
#   next to the reminder file), 185f29bd, f918266a, 9f3bb592.
# - Table values (grader_survey_multi R3): multi_apps 3e3fc409 (text years read as
#   "1999.0" when typed as numbers), d9b7c649.
# - Relative dates: chrome fc6d8143, 82bc8d6a, 1704f00f, b7895e80 and 5 more read the VM clock.
# - Listing/archive names: multi_apps f7dfbef3, 0e5303d4, 5df7b33a, 8df7e444, 82e3c869, 869de13e.
# - Byte-identical files: multi_apps 8df7e444, 415ef462, 82e3c869, ce2b64a2.
# - Program output: multi_apps 2373b66a (`sar -u 1 30`), f918266a, 26150609, 9219480b.
# - Settings/defaults: multi_apps 937087b6 (every video MIME type), 873cafdd, a74b607e, e1fc0df3.
# - Live window: thunderbird d38192b0 (compose window with an attachment), 15c3b339,
#   7b1e1ff9; multi_apps f5c13cdd (recipient pills), 48c46dc7.
# - Instruction files: multi_apps bc2b57f3, 8df7e444, eb303e01, aceb0368.
# - LibreOffice settings: impress 2cd43775, 0f84bef9, writer f178a4a9 (registrymodifications.xcu).
# - Exports: calc 3aaa4e37, aa3a8974, writer 4bcb1253, impress 455d3c66 (no Ctrl+S step).
# The last item gathers checks the calc/impress/writer/gimp/chrome/vlc/vscode/
# thunderbird skills each repeated.
TASK_RULES = (
    "\n\nHow to carry out the task:\n"
    "- A task worded as a question or a request for advice (\"How do I ...?\", \"Is there a way to "
    "...?\") is still a request to do it: make the change on the computer, do not only explain how.\n"
    "- When you set up software or a Python environment, install it where the user's default `python` and `pip` "
    "find it: checks run plain `python -c ...`, so a package inside a new virtualenv counts as not installed.\n"
    "- If the application the task is about is already open, work in that window: open files with its own "
    "File > Open and change its own settings. Do not start a second instance or relaunch it with the file -- "
    "the task is checked against the one that was open.\n"
    "- When the task says to do something from the command line or terminal, type those commands into the open "
    "terminal window (or open one), so they count as the user's terminal. Do not run them in a hidden subprocess "
    "from computer_use. \"The current directory\" is the one that terminal is already in.\n"
    "- Change the content that is there; do not invent text, items or values the user did not give (\"make these "
    "lines a list\" means formatting the existing paragraphs as list items, not writing "
    "new ones; any added format -- numbering, a style, a colour -- applies to the existing text). When correcting "
    "or reformatting a document or list, change only what is wrong: leave items "
    "that already comply exactly as they are, with their wording, capitalisation and optional parts. Keep each "
    "item's existing values unless they are wrong: replacing a correct value with another valid one, or dropping "
    "an optional part, is not a correction. Splitting or joining "
    "paragraphs keeps every character that was there, spaces included: put the break after the space that ends a "
    "sentence, do not replace \". \" with a break.\n"
    "- Write only what the task asks for, where it asks: an empty column, cell or field the task does not "
    "name stays empty, even when its header suggests a value, and a cell you cannot fill stays truly empty (not an "
    "empty string). A value you write into a table has the type of the column's existing cells: a number as a "
    "number, but a code or number that the neighbouring rows store as text as text. Put intermediate steps "
    "inside the one formula rather than in helper cells; the result is compared with the whole expected file.\n"
    "- When the task does not say where a new file goes, save it next to the file being processed -- the input "
    "the task works on (the file you convert, pack, extend or export from), not a separate instructions or notes "
    "file -- often on the Desktop. Use the name the task gives, and copy names exactly (case, spaces, "
    "extension), including a title or a person's name taken from the content. Do not create a new folder for it.\n"
    "- If the route the task names is blocked -- a site refuses you or shows a human-verification check, a "
    "dialog keeps failing -- do not switch to another site, program or source to make the result look done, "
    "and do not try to solve verification checks. Retry the named route a few times, then stop and say what "
    "blocked you. A blocked route is not an infeasible task: do not call declare_infeasible for it.\n"
    "- What is on screen when you start is part of the task: the setup may have opened the file at a "
    "particular slide, sheet, cell or tab. When the task says \"this\", \"these rows\" or \"the heading\" "
    "without naming where, it means that view; work there unless the task names another place. When the slide "
    "or page the file opened on has content matching the description, that is the one, even if another page "
    "also fits.\n"
    "- When the task names an object by what it looks like (\"the chart\", \"the logo\", \"the "
    "caption\"), look at it on screen and act on what a viewer would mean, not on the first match in the file "
    "structure; a shape hanging off the slide edge is usually a background.\n"
    "- A PDF form is filled through its form fields (pypdf's update_page_form_field_values, or the viewer's "
    "fields), not by drawing text on the page: checks read the field values.\n"
    "- When a check reads the terminal (its screen or history), leave each command and its output visible: do not "
    "clear the terminal or chain `clear` before the command.\n"
    "- A date given relative to today (a weekday, a number of days ahead, a day in a later month) is counted from "
    "the VM's own clock: run `date` first. A weekday named with \"next\" is its first occurrence after today.\n"
    "- When a check lists a folder or unpacks an archive, the names must match exactly and nothing extra may be "
    "there: no helper or leftover files, original names kept. An archive of a folder contains the folder itself "
    "(zip from its parent), not only its contents.\n"
    "- Files the task only moves, copies, renames, packs or extracts (such as a mail attachment) stay byte-identical: "
    "use cp, mv, zip or the mail client's Save Attachment, do not open and re-save them or re-encode images. "
    "When the task "
    "says rename, rename in place (a copy keeps the old name too).\n"
    "- A program's or command's output saved to a file is exactly its stdout, redirected, with the tool and "
    "arguments the task names and no prompt, label or extra line. Code that is to be fixed or extended is edited "
    "in place, keeping its file, module and function names.\n"
    "- Install software and change settings where the application itself records them (its own installer, "
    "settings dialog or documented config file), not through a look-alike. A \"default application\" covers the "
    "whole file category (every MIME type of that kind, not one extension).\n"
    "- When a check reads a live window, leave it in the state the task describes: a composed email stays open "
    "and unsent, a page the task says to stay on stays on screen.\n"
    "- When the task points to an instructions, notes or example file, do every item in it: the result is "
    "compared with the complete expected result, so a partly applied list scores 0.\n"
    "- LibreOffice application settings (anything under Tools > Options) are graded from "
    "its user profile (registrymodifications.xcu): set them in Tools > Options and press OK. A document style "
    "does not change an application default.\n"
    "- An export task is graded on the exported file at the path the task gives, with the default options and the "
    "content unchanged; no Ctrl+S follows it, so finish the export yourself.\n"
    "- After reordering, duplicating or moving slides, rows or list items, list them from the saved file by "
    "position and compare each with the requested order; when the screen does not show what you expected, find "
    "out why before you save.\n"
    "- Checking: inspect_file reads only the saved file, so save first, and trust it over the rendered screen "
    "for exact values (font size, colour, number format, cell value, bullets). Settings that apps keep in files "
    "(Chrome, VS Code, Thunderbird, VLC) are confirmed by reading those files. In dialogs, type exact numbers "
    "into fields rather than dragging sliders, and confirm the dialog closed. When LibreOffice asks whether to "
    "keep the current format on saving a .docx/.xlsx/.pptx, keep it."
)
