// Real HWND/UIA provider backed by a visible, selected RichTextBox.
// Deliberately reverses embedded membership and returns +/-37 for endpoint
// comparisons. Positions are fixed during document construction, not searched.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using System.Windows.Automation.Text;
using System.Windows.Forms;

// net48 has no managed ITextProvider2. ABI from Microsoft's UIAutomationCore.h:
// microsoft/win32metadata, generation/WinSDK/RecompiledIdlHeaders/um.
[ComVisible(true), Guid("0dc5e6ed-3e16-4bf1-8f9a-a979878bc195"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ITextProvider2Probe
{
    [return: MarshalAs(UnmanagedType.SafeArray, SafeArraySubType = VarEnum.VT_UNKNOWN)]
    ITextRangeProvider[] GetSelection();
    [return: MarshalAs(UnmanagedType.SafeArray, SafeArraySubType = VarEnum.VT_UNKNOWN)]
    ITextRangeProvider[] GetVisibleRanges();
    ITextRangeProvider RangeFromChild(IRawElementProviderSimple child);
    ITextRangeProvider RangeFromPoint(System.Windows.Point point);
    ITextRangeProvider DocumentRange { get; }

    SupportedTextSelection SupportedTextSelection { get; }

    ITextRangeProvider RangeFromAnnotation(IRawElementProviderSimple annotation);
    ITextRangeProvider GetCaretRange([MarshalAs(UnmanagedType.Bool)] out bool active);
}

class TextContextProbe : Form
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern IntPtr SendMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
    readonly string folder;
    readonly RichTextBox bodyControl = new RichTextBox
    {
        Dock = DockStyle.Fill,
        ReadOnly = true,
        HideSelection = false
    };
    readonly Button action = new Button
    {
        Text = "Independent embedded action",
        Dock = DockStyle.Bottom,
        Height = 30
    };
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer
    {
        Interval = 60
    };
    readonly JavaScriptSerializer serializer = new JavaScriptSerializer
    {
        MaxJsonLength = 2 * 1024 * 1024
    };
    Root provider;
    internal string Body = "", Mode = "normal";
    internal int SelectionStart, SelectionLength, Caret, Mutations, RangeCalls, CaretCalls, BodyCalls, SlowCalls, SlowCompleted, LayoutCalls;
    string eventCommand;
    DateTime eventDue;
    string eventKind;
    int eventVersion;
    internal bool EditorFocused;
    internal void NoteMutation()
    {
        Interlocked.Increment(ref Mutations);
    }

    internal void NoteRange()
    {
        Interlocked.Increment(ref RangeCalls);
    }

    internal void NoteCaret()
    {
        Interlocked.Increment(ref CaretCalls);
    }

    internal void NoteLayout()
    {
        Interlocked.Increment(ref LayoutCalls);
    }

    internal void NoteSlow()
    {
        Interlocked.Increment(ref SlowCalls);
    }

    internal void NoteSlowCompleted()
    {
        Interlocked.Increment(ref SlowCompleted);
    }

    internal void NoteBody()
    {
        Interlocked.Increment(ref BodyCalls);
    }

    internal Node[] Objects;
    DateTime deadline = DateTime.UtcNow.AddMinutes(15);
    protected override bool ShowWithoutActivation
    {
        get
        {
            return true;
        }
    }

    static string ReadCommand(string path)
    {
        try
        {
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var reader = new StreamReader(stream))
                return reader.ReadToEnd().Trim();
        }
        catch (IOException)
        {
            return null;
        }
    }

    TextContextProbe(string folder)
    {
        this.folder = folder;
        Directory.CreateDirectory(folder);
        Text = "Windows text context probe";
        Width = 840;
        Height = 620;
        Controls.Add(bodyControl);
        Controls.Add(action);
        Controls.Add(new Label { Text = "Provider description keeps independent identity", Dock = DockStyle.Top, Height = 24 });
        Controls.Add(new Label { Text = "Reading context", Dock = DockStyle.Top, Height = 32, Font = new Font(SystemFonts.MessageBoxFont.FontFamily, 14, FontStyle.Bold) });
        bodyControl.Font = new Font("Microsoft YaHei UI", 11);
        action.Click += (s, e) => Interlocked.Increment(ref Mutations);
        Shown += (s, e) =>
        {
            provider = new Root(this);
            SetBody(false);
        };
        timer.Tick += (s, e) =>
        {
            string command = Path.Combine(folder, "mode");
            string next = File.Exists(command) ? ReadCommand(command) : "normal";
            if (!new[]
            {
                "normal",
                "unsupported",
                "overlap",
                "disagree",
                "cycle",
                "long",
                "bad-caret",
                "compare-error",
                "bad-boundary",
                "deep-layout",
                "slow-hidden",
                "hard-block"
            }.Contains(next))
                next = Mode;
            if (next != Mode)
            {
                bool changed = (next == "long") != (Mode == "long");
                Mode = next;
                if (changed)
                    SetBody(next == "long");
                provider.Layout();
            }

            string eventFile = Path.Combine(folder, "event");
            if (File.Exists(eventFile))
            {
                string commandText = ReadCommand(eventFile);
                var parts = commandText == null ? new string[0] : commandText.Split(':');
                int delay;
                if (commandText != eventCommand && parts.Length >= 3 && Int32.TryParse(parts[1], out delay))
                {
                    eventCommand = commandText;
                    eventKind = parts[0];
                    eventDue = DateTime.UtcNow.AddMilliseconds(delay);
                }
            }

            if (eventKind != null && DateTime.UtcNow >= eventDue)
            {
                string send = eventKind;
                eventKind = null;
                if (send == "close")
                {
                    Close();
                    return;
                }

                string oldTitle = Text;
                Text = "Windows text context probe — state " + (++eventVersion);
                int count = send == "storm" ? 1000 : 1;
                for (int i = 0; i < count; i++)
                {
                    if (send == "structure")
                        AutomationInteropProvider.RaiseStructureChangedEvent(provider.Document, new StructureChangedEventArgs(StructureChangeType.ChildrenInvalidated, provider.Document.GetRuntimeId()));
                    else if (send == "text")
                        AutomationInteropProvider.RaiseAutomationEvent(TextPatternIdentifiers.TextChangedEvent, provider.Document, new AutomationEventArgs(TextPatternIdentifiers.TextChangedEvent));
                    else if (send == "selection")
                        AutomationInteropProvider.RaiseAutomationEvent(TextPatternIdentifiers.TextSelectionChangedEvent, provider.Document, new AutomationEventArgs(TextPatternIdentifiers.TextSelectionChangedEvent));
                    else if (send != "silent")
                        AutomationInteropProvider.RaiseAutomationPropertyChangedEvent(provider, new AutomationPropertyChangedEventArgs(AutomationElementIdentifiers.NameProperty, oldTitle, Text));
                }
            }

            string focus = Path.Combine(folder, "focus");
            if (File.Exists(focus))
            {
                File.Delete(focus);
                Activate();
                bodyControl.Focus();
            }

            string blur = Path.Combine(folder, "blur");
            if (File.Exists(blur))
            {
                File.Delete(blur);
                action.Focus();
            }

            SelectionStart = bodyControl.SelectionStart;
            SelectionLength = bodyControl.SelectionLength;
            Caret = SelectionStart + SelectionLength;
            EditorFocused = bodyControl.Focused;
            var expected = Objects.ToDictionary(item => item.Id, item => new { start = item.Start, end = item.End });
            var truth = new
            {
                pid = Process.GetCurrentProcess().Id,
                hwnd = Handle.ToInt64(),
                mode = Mode,
                body = bodyControl.Text,
                expected,
                selectionStart = SelectionStart,
                selectionLength = SelectionLength,
                selectedText = bodyControl.SelectedText,
                caret = Caret,
                editorFocused = EditorFocused,
                firstVisibleLine = SendMessage(bodyControl.Handle, 0xCE, IntPtr.Zero, IntPtr.Zero).ToInt32(),
                mutations = Mutations,
                rangeCalls = RangeCalls,
                caretCalls = CaretCalls,
                bodyCalls = BodyCalls,
                slowCalls = SlowCalls,
                slowCompleted = SlowCompleted,
                layoutCalls = LayoutCalls,
                title = Text,
                eventVersion,
                utcTicks = DateTime.UtcNow.Ticks,
                foreground = GetForegroundWindow().ToInt64()
            };
            File.WriteAllText(Path.Combine(folder, "truth.json"), serializer.Serialize(truth));
            if (DateTime.UtcNow > deadline || File.Exists(Path.Combine(folder, "stop")))
                Close();
        };
        timer.Start();
        FormClosed += (s, e) => timer.Stop();
    }

    void SetBody(bool longer)
    {
        var text = new StringBuilder("TEXT-CONTEXT-BEGIN 正文位置🙂 e\u0301\n重复内容 alpha\n");
        var objects = new List<Node>();
        int start = text.Length;
        text.Append("重复内容 alpha");
        objects.Add(new Node(provider, "link", "Repeated link", ControlType.Hyperlink.Id, start, text.Length));
        text.Append("\n重复内容 alpha\n");
        start = text.Length;
        text.Append("[Embedded button]");
        objects.Add(new Node(provider, "button", "Independent embedded action", ControlType.Button.Id, start, text.Length));
        text.Append("\n");
        start = text.Length;
        objects.Add(new Node(provider, "image", "Non-text image description", ControlType.Image.Id, start, start));
        text.Append("[Image: non-text description]\n");
        if (longer)
            text.Append(new string ('长', 70000) + "\n");
        start = text.Length;
        text.Append("Cell A\tCell A\n");
        objects.Add(new Node(provider, "table", "Embedded table", ControlType.Table.Id, start, text.Length));
        text.Append("TEXT-CONTEXT-END");
        // RichTextBox normalizes newlines; construction uses its actual LF text.
        Body = text.ToString();
        bodyControl.Text = Body;
        if (bodyControl.Text != Body)
            throw new InvalidOperationException("Unexpected native text normalization");
        bodyControl.Select(5, 9);
        Objects = objects.ToArray();
        provider.Document.Children = Objects.Reverse().ToArray();
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x003D && m.LParam.ToInt64() == AutomationInteropProvider.RootObjectId && provider != null)
        {
            m.Result = AutomationInteropProvider.ReturnRawElementProvider(Handle, m.WParam, m.LParam, provider);
            return;
        }

        base.WndProc(ref m);
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal class Node : IRawElementProviderSimple, IRawElementProviderFragment, IInvokeProvider
    {
        internal Root Root;
        internal readonly string Id, Label;
        internal readonly int Type, Start, End;
        internal Node[] Children = new Node[0];
        internal Node(Root root, string id, string label, int type, int start = 0, int end = 0)
        {
            Root = root;
            Id = id;
            Label = label;
            Type = type;
            Start = start;
            End = end;
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

        public virtual object GetPatternProvider(int id)
        {
            if ((id == 10014 || id == 10024) && Id == "document")
                return Root.Text;
            return id == 10000 && (Id == "button" || Id == "link") ? this : null;
        }

        public virtual object GetPropertyValue(int id)
        {
            if (id == 30003)
                return Type;
            if (id == 30005)
                return Id == "root" ? Root.Form.Text : Label;
            if (id == 30011)
                return Id;
            if (id == 30024)
                return "TextContextProbe";
            if (id == 30010 || id == 30016 || id == 30017)
                return true;
            if (id == 30019 || id == 30022)
                return false;
            if (id == 30008)
                return Id == "document" && Root.Form.EditorFocused;
            if (id == 30009)
                return Id == "document" || Id == "button" || Id == "link";
            if (id == 30173)
                return Id == "heading" ? 80052 : 80050;
            if (id == 30157)
                return Id == "document" ? 80002 : 0;
            if (id == 30158)
                return Id == "document" ? "Main reading area" : "";
            if (id == 30159)
                return Id == "document" ? "Native document with repeated text and independent objects" : "";
            if (id == 30105)
                return Id == "document" ? new IRawElementProviderSimple[]
                {
                    Root.Description
                }

                : new IRawElementProviderSimple[0];
            if (id == 30106)
            {
                if (Id == "heading")
                    return new IRawElementProviderSimple[]
                    {
                        Root.Document
                    };
                if (Id == "document")
                    return new IRawElementProviderSimple[]
                    {
                        Root.Description
                    };
                if (Id == "description" && Root.Form.Mode == "cycle")
                    return new IRawElementProviderSimple[]
                    {
                        Root.Heading
                    };
                return new IRawElementProviderSimple[0];
            }

            if (id == 30148)
                return new IRawElementProviderSimple[0];
            return null;
        }

        public virtual IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.FirstChild)
                return Children.FirstOrDefault();
            if (direction == NavigateDirection.LastChild)
                return Children.LastOrDefault();
            Node parent = Id == "root" ? null : new[]
            {
                "heading",
                "description",
                "document"
            }.Contains(Id) ? Root : Root.Document;
            if (direction == NavigateDirection.Parent)
                return parent;
            if (parent == null)
                return null;
            int index = Array.IndexOf(parent.Children, this) + (direction == NavigateDirection.NextSibling ? 1 : -1);
            return index >= 0 && index < parent.Children.Length ? parent.Children[index] : null;
        }

        public virtual int[] GetRuntimeId()
        {
            return Id == "root" ? null : new[]
            {
                AutomationInteropProvider.AppendRuntimeId,
                Array.IndexOf(new[] { "heading", "description", "document", "link", "button", "image", "table" }, Id) + 10
            };
        }

        public System.Windows.Rect BoundingRectangle
        {
            get
            {
                var b = Root.Form.Bounds;
                return new System.Windows.Rect(b.X + 10, b.Y + 30, b.Width - 20, b.Height - 50);
            }
        }

        public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
        {
            return null;
        }

        public void SetFocus()
        {
            Root.Form.NoteMutation();
            throw new InvalidOperationException("Reading requested focus");
        }

        public IRawElementProviderFragmentRoot FragmentRoot
        {
            get
            {
                return Root;
            }
        }

        public void Invoke()
        {
            Root.Form.NoteMutation();
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class Root : Node, IRawElementProviderFragmentRoot
    {
        internal readonly TextContextProbe Form;
        internal readonly Node Document, Heading, Description;
        internal readonly TextProvider Text;
        internal Root(TextContextProbe form) : base(null, "root", "Windows text context probe", ControlType.Window.Id)
        {
            Root = this;
            Form = form;
            Heading = new Node(this, "heading", "Reading context", ControlType.Text.Id);
            Description = new Node(this, "description", "Provider description keeps independent identity", ControlType.Text.Id);
            Document = new Node(this, "document", "Rich document", ControlType.Document.Id);
            Children = new[]
            {
                Heading,
                Description,
                Document
            };
            Text = new TextProvider(this);
        }

        internal void Layout()
        {
            Children = new[]
            {
                Heading,
                Description,
                Document
            };
            if (Form.Mode == "deep-layout")
            {
                var head = new LayoutNode(this, 0, this);
                var node = head;
                for (int i = 1; i < 180; i++)
                {
                    var child = new LayoutNode(this, i, node);
                    node.Children = new[]
                    {
                        child
                    };
                    node = child;
                }

                Children = new Node[]
                {
                    Heading,
                    Description,
                    Document,
                    head
                };
            }

            if (Form.Mode == "slow-hidden" || Form.Mode == "hard-block")
                Children = new Node[]
                {
                    new SlowNode(this),
                    Heading,
                    Description,
                    Document
                };
        }

        public override IRawElementProviderSimple HostRawElementProvider
        {
            get
            {
                return AutomationInteropProvider.HostProviderFromHandle(Form.Handle);
            }
        }

        public override object GetPropertyValue(int id)
        {
            return id == 30020 ? (object)Form.Handle.ToInt32() : base.GetPropertyValue(id);
        }

        public IRawElementProviderFragment ElementProviderFromPoint(double x, double y)
        {
            return Document;
        }

        public IRawElementProviderFragment GetFocus()
        {
            return Form.EditorFocused ? Document : null;
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class LayoutNode : Node
    {
        readonly int index;
        readonly Node parent;
        internal LayoutNode(Root root, int index, Node parent) : base(root, "layout-" + index, "", ControlType.Pane.Id)
        {
            this.index = index;
            this.parent = parent;
        }

        public override int[] GetRuntimeId()
        {
            return new[]
            {
                AutomationInteropProvider.AppendRuntimeId,
                1000 + index
            };
        }

        public override IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.Parent)
                return parent;
            if (direction == NavigateDirection.FirstChild)
            {
                Root.Form.NoteLayout();
                Thread.Sleep(25);
                return Children.FirstOrDefault();
            }

            if (direction == NavigateDirection.LastChild)
                return Children.LastOrDefault();
            return null;
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class SlowNode : Node, ITextProvider
    {
        internal SlowNode(Root root) : base(root, "slow-hidden", "Hidden diagnostic", ControlType.Edit.Id)
        {
        }

        public override int[] GetRuntimeId()
        {
            return new[]
            {
                AutomationInteropProvider.AppendRuntimeId,
                2000
            };
        }

        public override object GetPropertyValue(int id)
        {
            return id == 30022 ? (object)true : base.GetPropertyValue(id);
        }

        public override object GetPatternProvider(int id)
        {
            return id == 10014 ? this : null;
        }

        public override IRawElementProviderFragment Navigate(NavigateDirection direction)
        {
            if (direction == NavigateDirection.Parent)
                return Root;
            if (direction == NavigateDirection.NextSibling)
                return Root.Heading;
            return null;
        }

        public ITextRangeProvider DocumentRange
        {
            get
            {
                Root.Form.NoteSlow();
                try
                {
                    Thread.Sleep(Root.Form.Mode == "hard-block" ? 7000 : 2700);
                    return Root.Text.DocumentRange;
                }
                finally
                {
                    Root.Form.NoteSlowCompleted();
                }
            }
        }

        public SupportedTextSelection SupportedTextSelection
        {
            get
            {
                return SupportedTextSelection.Single;
            }
        }

        public ITextRangeProvider[] GetSelection()
        {
            return new ITextRangeProvider[0];
        }

        public ITextRangeProvider[] GetVisibleRanges()
        {
            return new ITextRangeProvider[0];
        }

        public ITextRangeProvider RangeFromChild(IRawElementProviderSimple child)
        {
            throw new NotSupportedException();
        }

        public ITextRangeProvider RangeFromPoint(System.Windows.Point point)
        {
            throw new NotSupportedException();
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class TextProvider : ITextProvider, ITextProvider2Probe
    {
        readonly Root root;
        internal TextProvider(Root root)
        {
            this.root = root;
        }

        public ITextRangeProvider DocumentRange
        {
            get
            {
                root.Form.NoteBody();
                return new Range(root, 0, root.Form.Body.Length);
            }
        }

        public SupportedTextSelection SupportedTextSelection
        {
            get
            {
                return SupportedTextSelection.Single;
            }
        }

        public ITextRangeProvider[] GetSelection()
        {
            return new ITextRangeProvider[]
            {
                new Range(root, root.Form.SelectionStart, root.Form.SelectionStart + root.Form.SelectionLength)
            };
        }

        public ITextRangeProvider[] GetVisibleRanges()
        {
            return new[]
            {
                DocumentRange
            };
        }

        public ITextRangeProvider RangeFromChild(IRawElementProviderSimple child)
        {
            root.Form.NoteRange();
            var item = child as Node;
            if (item == null || !root.Form.Objects.Contains(item))
                throw new ArgumentException("Not an embedded object");
            if (root.Form.Mode == "unsupported" && item.Id == "image")
                throw new NotSupportedException("Fixture has an unlocatable image");
            if (root.Form.Mode == "bad-boundary" && item.Id == "image")
            {
                int middle = "TEXT-CONTEXT-BEGIN 正文位置".Length + 1;
                return new Range(root, middle, middle);
            }

            return new Range(root, root.Form.Mode == "overlap" && item.Id == "image" ? root.Form.Objects[1].Start + 1 : item.Start, item.End);
        }

        public ITextRangeProvider RangeFromPoint(System.Windows.Point point)
        {
            return new Range(root, 0, 0);
        }

        public ITextRangeProvider RangeFromAnnotation(IRawElementProviderSimple annotation)
        {
            throw new NotSupportedException();
        }

        public ITextRangeProvider GetCaretRange(out bool active)
        {
            root.Form.NoteCaret();
            active = root.Form.EditorFocused;
            int caret = root.Form.Caret;
            return new Range(root, caret, root.Form.Mode == "bad-caret" ? caret + 1 : caret);
        }
    }

    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    internal sealed class Range : ITextRangeProvider
    {
        readonly Root root;
        int start, end;
        internal Range(Root root, int start, int end)
        {
            this.root = root;
            this.start = start;
            this.end = end;
        }

        int Endpoint(TextPatternRangeEndpoint endpoint)
        {
            return endpoint == TextPatternRangeEndpoint.Start ? start : end;
        }

        public ITextRangeProvider Clone()
        {
            return new Range(root, start, end);
        }

        public bool Compare(ITextRangeProvider other)
        {
            var value = (Range)other;
            return start == value.start && end == value.end;
        }

        public int CompareEndpoints(TextPatternRangeEndpoint endpoint, ITextRangeProvider other, TextPatternRangeEndpoint otherEndpoint)
        {
            var value = (Range)other;
            if (root.Form.Mode == "compare-error" && !ReferenceEquals(this, value) && start > 0 && value.start > 0)
                throw new InvalidOperationException("Provider cannot compare independent object ranges");
            return Math.Sign(Endpoint(endpoint) - value.Endpoint(otherEndpoint)) * 37;
        }

        public void ExpandToEnclosingUnit(TextUnit unit)
        {
            start = 0;
            end = root.Form.Body.Length;
        }

        public ITextRangeProvider FindAttribute(int attribute, object value, bool backward)
        {
            throw new NotSupportedException();
        }

        public ITextRangeProvider FindText(string text, bool backward, bool ignoreCase)
        {
            throw new InvalidOperationException("Reading must not search repeated text to locate objects");
        }

        public object GetAttributeValue(int attribute)
        {
            return attribute == 40015 ? (object)true : attribute == 40034 ? (object)70000 : AutomationElement.NotSupported;
        }

        public double[] GetBoundingRectangles()
        {
            return new double[0];
        }

        public IRawElementProviderSimple GetEnclosingElement()
        {
            return root.Document;
        }

        public string GetText(int maxLength)
        {
            string value = root.Form.Body.Substring(start, end - start);
            if (root.Form.Mode == "disagree" && start == 0 && end == root.Form.Objects[0].Start)
                value = "wrong prefix";
            return maxLength < 0 ? value : value.Substring(0, Math.Min(value.Length, maxLength));
        }

        public int Move(TextUnit unit, int count)
        {
            throw new NotSupportedException();
        }

        public int MoveEndpointByUnit(TextPatternRangeEndpoint endpoint, TextUnit unit, int count)
        {
            throw new InvalidOperationException("Reading must not treat native character units as UTF-16 offsets");
        }

        public void MoveEndpointByRange(TextPatternRangeEndpoint endpoint, ITextRangeProvider other, TextPatternRangeEndpoint otherEndpoint)
        {
            int position = ((Range)other).Endpoint(otherEndpoint);
            if (endpoint == TextPatternRangeEndpoint.Start)
            {
                start = position;
                if (end < start)
                    end = start;
            }
            else
            {
                end = position;
                if (start > end)
                    start = end;
            }
        }

        public IRawElementProviderSimple[] GetChildren()
        {
            return root.Form.Objects.Reverse().Cast<IRawElementProviderSimple>().ToArray();
        }

        void Reject()
        {
            root.Form.NoteMutation();
            throw new InvalidOperationException("Read mutated selection or scroll");
        }

        public void Select()
        {
            Reject();
        }

        public void AddToSelection()
        {
            Reject();
        }

        public void RemoveFromSelection()
        {
            Reject();
        }

        public void ScrollIntoView(bool alignToTop)
        {
            Reject();
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.Run(new TextContextProbe(args[0]));
    }
}
