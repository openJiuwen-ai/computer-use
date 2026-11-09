// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal sealed class WindowsEditCoverageFixture : Form
{
    private sealed class EventEdit : TextBox
    {
        internal int SetTextMessages, PasteMessages, SelectMessages, ReplaceMessages;

        protected override void WndProc(ref Message message)
        {
            if (message.Msg == 0x000C) // WM_SETTEXT
                SetTextMessages++;
            if (message.Msg == 0x0302) // WM_PASTE
                PasteMessages++;
            if (message.Msg == 0x00B1) // EM_SETSEL
                SelectMessages++;
            if (message.Msg == 0x00C2) // EM_REPLACESEL
                ReplaceMessages++;
            base.WndProc(ref message);
        }
    }

    private sealed class EditField
    {
        internal string text;
        internal string submittedText;
        internal bool selected;
        internal int clicks;
        internal int selections;
        internal int units;
        internal int submits;

        internal object Snapshot()
        {
            return new { text, submittedText, clicks, selections, units, submits };
        }
    }

    private readonly EditField target = new EditField { text = "Original draft value" };
    private readonly EditField bystander = new EditField { text = "Untouched companion value" };
    private readonly Rectangle targetBounds;
    private readonly Rectangle bystanderBounds;
    private readonly TextBox treeTarget;
    private readonly TextBox treeBystander;
    private readonly Label treeStatus;
    private readonly Button eventSubmit;
    private readonly bool eventMessages;
    private bool eventReady;
    private int nativeChanges;
    private int treeChanges, treeBystanderChanges, treeSubmits;
    private string treeSubmitted;
    private EditField active;

    private WindowsEditCoverageFixture(string title, string tier, bool swap, bool eventMessages)
    {
        this.eventMessages = eventMessages;
        Text = title;
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(550, 210);
        BackColor = Color.FromArgb(29, 35, 46);
        DoubleBuffered = true;
        KeyPreview = true;

        targetBounds = new Rectangle(swap ? 282 : 16, 52, 252, 90);
        bystanderBounds = new Rectangle(swap ? 16 : 282, 52, 252, 90);
        bool eventDependent = tier == "full-tree-event";
        if (tier == "full-tree" || tier == "partial-tree" || eventDependent)
        {
            treeTarget = eventDependent ? new EventEdit() : new TextBox();
            treeTarget.AccessibleName = "Editable draft";
            treeTarget.Text = target.text;
            treeTarget.Multiline = true;
            treeTarget.Bounds = targetBounds;
            treeTarget.TextChanged += (sender, args) =>
            {
                treeChanges++;
                if (eventReady)
                    eventSubmit.Enabled = true;
            };
            treeTarget.Enter += (sender, args) => active = null;
            Controls.Add(treeTarget);
            if (tier == "full-tree" || eventDependent)
            {
                treeBystander = new TextBox { AccessibleName = "Protected companion", Text = bystander.text,
                                              Bounds = bystanderBounds };
                treeBystander.TextChanged += (sender, args) => treeBystanderChanges++;
                Controls.Add(treeBystander);
            }
            treeStatus = new Label { Text = "Draft pending", AccessibleName = "Draft pending",
                                     Bounds = new Rectangle(156, 159, 350, 30), ForeColor = Color.White };
            var submit = new Button { Text = "Submit draft", AccessibleName = "Submit draft",
                                      Bounds = new Rectangle(16, 156, 125, 34), Enabled = !eventDependent };
            if (eventDependent)
            {
                eventSubmit = submit;
                Shown += (sender, args) =>
                {
                    ActiveControl = treeTarget;
                    if (eventMessages)
                        treeTarget.Select(0, 0);
                    else
                        treeTarget.SelectAll();
                    var edit = (EventEdit)treeTarget;
                    edit.SetTextMessages = edit.PasteMessages = edit.SelectMessages = edit.ReplaceMessages = 0;
                    treeChanges = nativeChanges = 0;
                    eventReady = true;
                };
            }
            submit.Click += (sender, args) =>
            {
                treeSubmits++;
                treeSubmitted = treeTarget.Text;
                treeStatus.Text = treeStatus.AccessibleName = "Draft submitted " + treeSubmits;
            };
            Controls.Add(treeStatus);
            Controls.Add(submit);
        }
        else if (tier == "partial")
        {
            var context = new Label {
                Text = "CUA draft workspace context",
                AccessibleName = "CUA draft workspace context",
                Bounds = new Rectangle(16, 14, 300, 24),
                ForeColor = Color.White
            };
            Controls.Add(context);
        }
        if (!eventMessages)
            Shown += (sender, args) => Activate();
    }

    protected override bool ShowWithoutActivation { get { return eventMessages || base.ShowWithoutActivation; } }

    protected override void WndProc(ref Message message)
    {
        // Count the edit's native notification before WinForms reflects it.
        if (eventReady && message.Msg == 0x0111 && treeTarget.IsHandleCreated &&
            message.LParam == treeTarget.Handle && ((message.WParam.ToInt64() >> 16) & 0xFFFF) == 0x0300)
            nativeChanges++;
        base.WndProc(ref message);
    }

    protected override void OnPaint(PaintEventArgs args)
    {
        base.OnPaint(args);
        if (treeTarget != null)
        {
            if (treeBystander == null)
                DrawField(args.Graphics, bystanderBounds, bystander, "Protected companion", Color.FromArgb(216, 56, 37));
            return;
        }
        DrawField(args.Graphics, targetBounds, target, "Visual edit target", Color.FromArgb(13, 237, 101));
        DrawField(args.Graphics, bystanderBounds, bystander, "Protected companion", Color.FromArgb(216, 56, 37));
        if (target.submits != 0)
        {
            using (var submitted = new SolidBrush(Color.FromArgb(147, 54, 235)))
                args.Graphics.FillRectangle(submitted, 16, 158, 34, 30);
            args.Graphics.DrawString("Submitted " + target.submits + ": " + target.submittedText,
                                     Font, Brushes.White, 60, 165);
        }
    }

    private void DrawField(Graphics graphics, Rectangle bounds, EditField field, string title, Color marker)
    {
        graphics.FillRectangle(field == active ? Brushes.MidnightBlue : Brushes.Black, bounds);
        using (var brush = new SolidBrush(marker))
            graphics.FillRectangle(brush, bounds.Left + 12, bounds.Top + 16, 40, 40);
        graphics.DrawString(title, Font, Brushes.White, bounds.Left + 63, bounds.Top + 16);
        graphics.DrawString(field.text, Font, Brushes.White, bounds.Left + 63, bounds.Top + 42);
    }

    protected override void OnMouseDown(MouseEventArgs args)
    {
        base.OnMouseDown(args);
        if (args.Button != MouseButtons.Left)
            return;
        if (treeTarget == null && targetBounds.Contains(args.Location))
            active = target;
        else if (treeBystander == null && bystanderBounds.Contains(args.Location))
            active = bystander;
        else
            active = null;
        if (active == null)
            return;
        active.clicks++;
        active.selected = false;
        Focus();
        Invalidate();
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if (active != null && keyData == (Keys.Control | Keys.A))
        {
            active.selected = true;
            active.selections++;
            return true;
        }
        if (active != null && keyData == Keys.Return)
        {
            active.submittedText = active.text;
            active.submits++;
            Invalidate();
            return true;
        }
        return base.ProcessCmdKey(ref message, keyData);
    }

    protected override void OnKeyPress(KeyPressEventArgs args)
    {
        base.OnKeyPress(args);
        if (active == null || char.IsControl(args.KeyChar))
            return;
        if (active.selected)
            active.text = "";
        active.selected = false;
        active.text += args.KeyChar;
        active.units++;
        args.Handled = true;
        Invalidate();
    }

    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        bool eventMessages = args[2] == "full-tree-event" && args.Length == 5 && args[4] == "messages";
        using (var form = new WindowsEditCoverageFixture(args[1], args[2], args[3] == "right", eventMessages))
        using (var timer = new Timer { Interval = 50 })
        {
            int sample = 0;
            var json = new JavaScriptSerializer();
            timer.Tick += (sender, eventArgs) =>
            {
                if (form.eventSubmit != null)
                {
                    var edit = (EventEdit)form.treeTarget;
                    File.WriteAllText(args[0], json.Serialize(new {
                        sample = ++sample, text = edit.Text, changes = form.treeChanges,
                        submitted = form.treeSubmitted, submits = form.treeSubmits, submitEnabled = form.eventSubmit.Enabled,
                        bystander = form.treeBystander.Text, bystanderChanges = form.treeBystanderChanges,
                        wmSetText = edit.SetTextMessages, enChange = form.nativeChanges, wmPaste = edit.PasteMessages,
                        emSetSel = edit.SelectMessages, emReplaceSel = edit.ReplaceMessages,
                        focused = edit.Focused, selectionLength = edit.SelectionLength,
                        rootHwnd = form.Handle.ToInt64(), editHwnd = edit.Handle.ToInt64(),
                        companionHwnd = form.treeBystander.Handle.ToInt64()
                    }));
                    return;
                }
                if (form.treeTarget != null)
                {
                    File.WriteAllText(args[0], json.Serialize(new {
                        sample = ++sample, text = form.treeTarget.Text, changes = form.treeChanges,
                        submitted = form.treeSubmitted, submits = form.treeSubmits,
                        bystander = form.treeBystander == null ? form.bystander.text : form.treeBystander.Text,
                        bystanderChanges = form.treeBystanderChanges, paintedBystander = form.bystander.Snapshot()
                    }));
                    return;
                }
                File.WriteAllText(args[0], json.Serialize(new {
                    sample = ++sample,
                    active = form.active == form.target ? "target" : form.active == form.bystander ? "bystander" : "none",
                    target = form.target.Snapshot(),
                    bystander = form.bystander.Snapshot()
                }));
            };
            timer.Start();
            Application.Run(form);
        }
    }
}
