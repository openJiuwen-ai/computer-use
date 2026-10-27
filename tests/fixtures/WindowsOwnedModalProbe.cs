// Own native HWNDs plus logical UIA modal descendants. No application-name policy.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using System.Windows.Forms;

internal sealed class WindowsOwnedModalProbe : Form
{
    readonly string folder;
    readonly bool parallelMode;
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    internal Dialog modal, sibling;
    Form unrelated;
    internal Root root;
    readonly TextBox guard;
    internal int Accepted, NestedAccepted, BackgroundClicks, Serial, SkippedWrites;
    internal string Typed = "", NestedTyped = "";
    internal WindowsOwnedModalProbe(string folder, bool parallelMode)
    {
        this.folder = folder;
        this.parallelMode = parallelMode;
        Directory.CreateDirectory(folder);
        Text = "Computer Use modal receiver fixture";
        ClientSize = new Size(900, 600);
        Location = new Point(100, 80);
        StartPosition = FormStartPosition.Manual;
        var open = new Button
        {
            Text = "Open native modal",
            Location = new Point(20, 35),
            Size = new Size(230, 45)
        };
        var background = new Button
        {
            Text = "Background guard",
            Location = new Point(20, 100),
            Size = new Size(230, 45)
        };
        guard = new TextBox
        {
            AccessibleName = "Background editor",
            Multiline = true,
            Text = "KEEP_UNCHANGED",
            Location = new Point(290, 100),
            Size = new Size(260, 60)
        };
        open.Click += delegate
        {
            OpenModal();
        };
        background.Click += delegate
        {
            BackgroundClicks++;
        };
        Controls.Add(open);
        Controls.Add(background);
        Controls.Add(guard);
        Controls.Add(new Label { Text = "Modal descendants belong to their real native input window.\nBackground controls must remain blocked.", Location = new Point(20, 175), AutoSize = true });
        Shown += delegate
        {
            root = new Root(this, null);
            Save("ready.json");
            timer.Start();
        };
        timer.Interval = 50;
        timer.Tick += delegate
        {
            Save("state.json");
            if (File.Exists(Path.Combine(folder, "stop")))
            {
                timer.Stop();
                if (sibling != null)
                    sibling.Close();
                if (modal != null)
                {
                    if (modal.child != null)
                        modal.child.Close();
                    modal.Close();
                }

                // Let ShowDialog unwind and clear its field before closing the
                // owner. Reading a disposed dialog's Handle can recreate it.
                BeginInvoke((Action)(() => Close()));
            }
        };
        FormClosed += delegate
        {
            timer.Stop();
            if (unrelated != null)
            {
                unrelated.Close();
                unrelated = null;
            }

            Save("final.json");
        };
    }

    internal void Run(Action action)
    {
        if (InvokeRequired)
            BeginInvoke(action);
        else
            action();
    }

    void OpenModal()
    {
        if (modal != null)
            return;
        modal = new Dialog(this, null, false);
        if (parallelMode)
        {
            sibling = new Dialog(this, null, true);
            Enabled = false;
            modal.FormClosed += delegate
            {
                modal = null;
                if (sibling == null)
                    Enabled = true;
            };
            sibling.FormClosed += delegate
            {
                sibling = null;
                if (modal == null)
                    Enabled = true;
            };
            modal.Show(this);
            sibling.Show(this);
        }
        else
        {
            modal.ShowDialog(this);
            modal.Dispose();
            modal = null;
        }
    }

    void Detach(Dialog dialog)
    {
        unrelated = new Form
        {
            Text = "Computer Use unrelated fixture window",
            Location = new Point(1050, 100),
            Size = new Size(300, 180),
            StartPosition = FormStartPosition.Manual
        };
        unrelated.Show();
        SetWindowLongPtr(dialog.Handle, -8, unrelated.Handle);
        Enabled = true;
        Save("detached-ready.json");
    }

    internal void Save(string name)
    {
        var state = new
        {
            pid = Process.GetCurrentProcess().Id,
            owner = Handle.ToInt64(),
            dialog = modal == null ? 0 : modal.Handle.ToInt64(),
            nested = modal == null || modal.child == null ? 0 : modal.child.Handle.ToInt64(),
            sibling = sibling == null ? 0 : sibling.Handle.ToInt64(),
            unrelated = unrelated == null ? 0 : unrelated.Handle.ToInt64(),
            accepted = Accepted,
            nestedAccepted = NestedAccepted,
            backgroundClicks = BackgroundClicks,
            typed = Typed,
            nestedTyped = NestedTyped,
            guard = guard.Text,
            serial = Serial,
            skippedWrites = SkippedWrites,
            utc = DateTime.UtcNow.ToString("o")
        };
        string path = Path.Combine(folder, name), stage = path + ".tmp";
        try
        {
            File.WriteAllText(stage, new JavaScriptSerializer().Serialize(state), new UTF8Encoding(false));
            if (File.Exists(path))
                File.Replace(stage, path, null);
            else
                File.Move(stage, path);
        }
        catch (IOException)
        {
            SkippedWrites++;
        }
        catch (UnauthorizedAccessException)
        {
            SkippedWrites++;
        }
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x3d && m.LParam.ToInt64() == AutomationInteropProvider.RootObjectId && root != null)
        {
            m.Result = AutomationInteropProvider.ReturnRawElementProvider(Handle, m.WParam, m.LParam, root);
            return;
        }

        base.WndProc(ref m);
    }

    internal sealed class Dialog : Form
    {
        internal readonly WindowsOwnedModalProbe owner;
        internal readonly Dialog parent;
        internal readonly int ordinal;
        internal readonly Fragment logical;
        internal Root nativeRoot;
        internal Dialog child;
        internal Dialog(WindowsOwnedModalProbe owner, Dialog parent, bool sibling)
        {
            this.owner = owner;
            this.parent = parent;
            ordinal = ++owner.Serial;
            Text = parent != null ? "Computer Use nested native modal" : sibling ? "Computer Use sibling native modal" : "Computer Use owned native modal";
            ClientSize = new Size(480, 345);
            Location = parent == null ? new Point(350, 210) : new Point(450, 280);
            StartPosition = FormStartPosition.Manual;
            var accept = new Button
            {
                Text = parent == null ? "Accept in modal" : "Accept in nested modal",
                Location = new Point(25, 25),
                Size = new Size(210, 40)
            };
            var edit = new TextBox
            {
                AccessibleName = parent == null ? "Modal editor" : "Nested editor",
                Multiline = true,
                Location = new Point(25, 90),
                Size = new Size(420, 100)
            };
            var close = new Button
            {
                Text = "Close modal",
                Location = new Point(25, 220),
                Size = new Size(200, 40)
            };
            accept.Click += delegate
            {
                if (parent == null)
                    owner.Accepted++;
                else
                    owner.NestedAccepted++;
            };
            edit.TextChanged += delegate
            {
                if (parent == null)
                    owner.Typed = edit.Text;
                else
                    owner.NestedTyped = edit.Text;
            };
            close.Click += delegate
            {
                Close();
            };
            Controls.Add(accept);
            Controls.Add(edit);
            Controls.Add(close);
            if (parent == null && !sibling)
            {
                var nested = new Button
                {
                    Text = "Open nested modal",
                    Location = new Point(25, 280),
                    Size = new Size(200, 40)
                };
                nested.Click += delegate
                {
                    child = new Dialog(owner, this, false);
                    child.ShowDialog(this);
                    child.Dispose();
                    child = null;
                };
                Controls.Add(nested);
                var detach = new Button
                {
                    Text = "Detach modal owner",
                    Location = new Point(245, 280),
                    Size = new Size(200, 40)
                };
                detach.Click += delegate
                {
                    owner.Detach(this);
                };
                Controls.Add(detach);
            }

            logical = new Fragment(owner, this);
            Shown += delegate
            {
                nativeRoot = new Root(owner, this);
                owner.Save(parent == null ? "modal-ready.json" : "nested-ready.json");
            };
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == 0x3d && m.LParam.ToInt64() == AutomationInteropProvider.RootObjectId && nativeRoot != null)
            {
                m.Result = AutomationInteropProvider.ReturnRawElementProvider(Handle, m.WParam, m.LParam, nativeRoot);
                return;
            }

            base.WndProc(ref m);
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal class Fragment : IRawElementProviderSimple, IRawElementProviderFragment, IWindowProvider
    {
        protected readonly WindowsOwnedModalProbe owner;
        protected readonly Dialog dialog;
        internal Fragment(WindowsOwnedModalProbe owner, Dialog dialog)
        {
            this.owner = owner;
            this.dialog = dialog;
        }

        internal Control WindowControl
        {
            get
            {
                return dialog == null ? (Control)owner : dialog;
            }
        }

        Fragment Parent
        {
            get
            {
                return dialog == null ? null : dialog.parent == null ? (Fragment)owner.root : dialog.parent.logical;
            }
        }

        internal virtual List<Fragment> Children()
        {
            var children = new List<Fragment>();
            if (dialog == null)
            {
                if (owner.modal != null)
                    children.Add(owner.modal.logical);
                if (owner.sibling != null)
                    children.Add(owner.sibling.logical);
            }
            else if (dialog.child != null)
                children.Add(dialog.child.logical);
            return children;
        }

        public ProviderOptions ProviderOptions
        {
            get
            {
                return ProviderOptions.ServerSideProvider;
            }
        }

        public virtual IRawElementProviderSimple HostRawElementProvider
        {
            get
            {
                return null;
            }
        }

        public object GetPatternProvider(int pattern)
        {
            return pattern == WindowPattern.Pattern.Id ? this : null;
        }

        public object GetPropertyValue(int property)
        {
            if (property == AutomationElement.NativeWindowHandleProperty.Id)
                return WindowControl.Handle.ToInt32();
            if (property == AutomationElement.ProcessIdProperty.Id)
                return Process.GetCurrentProcess().Id;
            if (property == AutomationElement.ControlTypeProperty.Id)
                return ControlType.Window.Id;
            if (property == AutomationElement.NameProperty.Id)
                return WindowControl.Text;
            if (property == AutomationElement.AutomationIdProperty.Id)
                return "OwnModalWindow" + (dialog == null ? 0 : dialog.ordinal);
            if (property == AutomationElement.IsEnabledProperty.Id)
                return WindowControl.Enabled;
            if (property == AutomationElement.IsOffscreenProperty.Id)
                return !WindowControl.Visible;
            if (property == AutomationElement.IsControlElementProperty.Id || property == AutomationElement.IsContentElementProperty.Id)
                return true;
            if (property == AutomationElement.IsKeyboardFocusableProperty.Id)
                return false;
            return null;
        }

        public IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.Parent)
                return Parent;
            var children = Children();
            if (direction == NavigateDirection.FirstChild)
                return children.Count == 0 ? null : children[0];
            if (direction == NavigateDirection.LastChild)
                return children.Count == 0 ? null : children[children.Count - 1];
            var parent = Parent;
            if (parent == null)
                return null;
            var peers = parent.Children();
            int at = peers.IndexOf(this), next = direction == NavigateDirection.NextSibling ? at + 1 : at - 1;
            return at < 0 || next < 0 || next >= peers.Count ? null : peers[next];
        }

        public virtual int[] GetRuntimeId()
        {
            return new[]
            {
                AutomationInteropProvider.AppendRuntimeId,
                Process.GetCurrentProcess().Id,
                dialog == null ? 0 : dialog.ordinal
            };
        }

        public System.Windows.Rect BoundingRectangle
        {
            get
            {
                var box = WindowControl.RectangleToScreen(WindowControl.ClientRectangle);
                return new System.Windows.Rect(box.X, box.Y, box.Width, box.Height);
            }
        }

        public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
        {
            return null;
        }

        public void SetFocus()
        {
            owner.Run(() => WindowControl.Focus());
        }

        public virtual IRawElementProviderFragmentRoot FragmentRoot
        {
            get
            {
                return owner.root;
            }
        }

        public bool Maximizable
        {
            get
            {
                return false;
            }
        }

        public bool Minimizable
        {
            get
            {
                return false;
            }
        }

        public bool IsModal
        {
            get
            {
                return dialog != null;
            }
        }

        public bool IsTopmost
        {
            get
            {
                return false;
            }
        }

        public WindowInteractionState InteractionState
        {
            get
            {
                return WindowControl.Enabled ? WindowInteractionState.ReadyForUserInteraction : WindowInteractionState.BlockedByModalWindow;
            }
        }

        public WindowVisualState VisualState
        {
            get
            {
                return WindowVisualState.Normal;
            }
        }

        public void Close()
        {
            owner.Run(() => ((Form)WindowControl).Close());
        }

        public void SetVisualState(WindowVisualState state)
        {
            if (state != WindowVisualState.Normal)
                throw new InvalidOperationException();
        }

        public bool WaitForInputIdle(int milliseconds)
        {
            return true;
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class Root : Fragment, IRawElementProviderFragmentRoot
    {
        internal Root(WindowsOwnedModalProbe owner, Dialog dialog) : base(owner, dialog)
        {
        }

        public override IRawElementProviderSimple HostRawElementProvider
        {
            get
            {
                return AutomationInteropProvider.HostProviderFromHandle(WindowControl.Handle);
            }
        }

        public override int[] GetRuntimeId()
        {
            return null;
        }

        public override IRawElementProviderFragmentRoot FragmentRoot
        {
            get
            {
                return this;
            }
        }

        public IRawElementProviderFragment ElementProviderFromPoint(double x, double y)
        {
            foreach (var child in Children())
                if (child.BoundingRectangle.Contains(x, y))
                    return child;
            return this;
        }

        public IRawElementProviderFragment GetFocus()
        {
            return this;
        }
    }

    [DllImport("user32.dll")]
    static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    static extern bool SetProcessDPIAware();
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
    static extern IntPtr SetWindowLongPtr(IntPtr window, int index, IntPtr value);
    [STAThread]
    static void Main(string[] args)
    {
        try
        {
            SetProcessDpiAwarenessContext(new IntPtr(-4));
        }
        catch (EntryPointNotFoundException)
        {
            SetProcessDPIAware();
        }

        Application.EnableVisualStyles();
        Application.Run(new WindowsOwnedModalProbe(args[0], args.Length > 1 && args[1] == "parallel"));
    }
}
