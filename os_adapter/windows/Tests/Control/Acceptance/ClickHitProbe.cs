using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using System.Windows.Forms;
using System.Web.Script.Serialization;

// Disposable real UIA server: several virtual controls share ONE HWND.
// The deliberately stale clickable point exposes the same-window routing bug.
// This process never injects mouse/keyboard input.
class ClickHitSurface : Control
{
    internal readonly ClickHitRoot Provider;
    internal ClickHitSurface()
    {
        Provider = new ClickHitRoot(this);
        BackColor = Color.WhiteSmoke;
        SetStyle(ControlStyles.StandardClick, true);
    }

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == 0x003d && m.LParam.ToInt64() == -25)
        {
            m.Result = AutomationInteropProvider.ReturnRawElementProvider(Handle, m.WParam, m.LParam, Provider);
            return;
        }

        base.WndProc(ref m);
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        ClickHitProbe.Mouse("down", Provider.Receiver(e.X, e.Y));
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        ClickHitProbe.Mouse("up", Provider.Receiver(e.X, e.Y));
    }

    protected override void OnMouseClick(MouseEventArgs e)
    {
        base.OnMouseClick(e);
        ClickHitProbe.Mouse("click", Provider.Receiver(e.X, e.Y));
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        e.Graphics.DrawString("Click boundary: " + ClickHitProbe.Mode, Font, Brushes.Black, 18, 12);
        foreach (var n in Provider.Nodes)
        {
            if (n.Id == 2 && !ClickHitProbe.Covered || n.Id == 4)
                continue;
            var b = n.Local;
            e.Graphics.FillRectangle(n.Id == 2 ? Brushes.Salmon : n.Id == 3 ? Brushes.LightBlue : Brushes.LightGreen, b);
            e.Graphics.DrawRectangle(Pens.Black, b);
            e.Graphics.DrawString(n.Name, Font, Brushes.Black, b.X + 8, b.Y + 12);
        }

        e.Graphics.DrawString(ClickHitProbe.CountSummary(), Font, Brushes.Black, 18, 275);
    }
}

class ClickHitForm : Form
{
    protected override bool ShowWithoutActivation
    {
        get
        {
            return true;
        }
    }
}

[ComVisible(true)]
public class ClickHitRoot : IRawElementProviderFragmentRoot
{
    internal readonly ClickHitSurface Surface;
    internal readonly ClickHitNode[] Nodes;
    internal ClickHitRoot(ClickHitSurface surface)
    {
        Surface = surface;
        Nodes = new[]
        {
            new ClickHitNode(this, 1, "Pointer target A"),
            new ClickHitNode(this, 2, "Overlay control B"),
            new ClickHitNode(this, 3, "Invoke-only document row"),
            new ClickHitNode(this, 4, "Coarse underlying page")
        };
    }

    internal string Receiver(int x, int y)
    {
        if (ClickHitProbe.Covered && Nodes[1].Local.Contains(x, y))
            return "B";
        if (Nodes[0].Local.Contains(x, y))
            return "A";
        if (Nodes[2].Local.Contains(x, y))
            return "row";
        return "background";
    }

    public IRawElementProviderFragment ElementProviderFromPoint(double x, double y)
    {
        var p = Surface.PointToClient(new System.Drawing.Point((int)x, (int)y));
        if (ClickHitProbe.Mode == "ancestor")
            return this;
        if (ClickHitProbe.Mode == "coarse")
            return Nodes[3];
        if (ClickHitProbe.Mode == "query-failed")
            throw new COMException("Deliberate hit query failure", unchecked((int)0x80004005));
        string hit = Receiver(p.X, p.Y);
        return hit == "A" ? Nodes[0] : hit == "B" ? Nodes[1] : hit == "row" ? Nodes[2] : (IRawElementProviderFragment)this;
    }

    public IRawElementProviderFragment GetFocus()
    {
        return null;
    }

    public ProviderOptions ProviderOptions
    {
        get
        {
            return ProviderOptions.ServerSideProvider;
        }
    }

    public IRawElementProviderSimple HostRawElementProvider
    {
        get
        {
            return AutomationInteropProvider.HostProviderFromHandle(Surface.Handle);
        }
    }

    public object GetPatternProvider(int id)
    {
        return null;
    }

    public object GetPropertyValue(int id)
    {
        if (id == AutomationElementIdentifiers.ControlTypeProperty.Id)
            return ControlType.Pane.Id;
        if (id == AutomationElementIdentifiers.NameProperty.Id)
            return "Click hit surface";
        if (id == AutomationElementIdentifiers.IsEnabledProperty.Id || id == AutomationElementIdentifiers.IsControlElementProperty.Id || id == AutomationElementIdentifiers.IsContentElementProperty.Id)
            return true;
        if (id == AutomationElementIdentifiers.NativeWindowHandleProperty.Id)
            return Surface.Handle.ToInt32();
        return null;
    }

    public Rect BoundingRectangle
    {
        get
        {
            var b = Surface.RectangleToScreen(Surface.ClientRectangle);
            return new Rect(b.X, b.Y, b.Width, b.Height);
        }
    }

    public IRawElementProviderFragmentRoot FragmentRoot
    {
        get
        {
            return this;
        }
    }

    public IRawElementProviderFragment Navigate(NavigateDirection d)
    {
        return d == NavigateDirection.FirstChild ? Nodes[0] : d == NavigateDirection.LastChild ? Nodes[Nodes.Length - 1] : null;
    }

    public int[] GetRuntimeId()
    {
        return null;
    }

    public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
    {
        return null;
    }

    public void SetFocus()
    {
    }
}

[ComVisible(true)]
public class ClickHitNode : IRawElementProviderFragment, IInvokeProvider
{
    internal readonly ClickHitRoot Root;
    internal readonly int Id;
    internal readonly string Name;
    internal ClickHitNode(ClickHitRoot root, int id, string name)
    {
        Root = root;
        Id = id;
        Name = name;
    }

    internal Rectangle Local
    {
        get
        {
            if (Id == 2)
                return ClickHitProbe.Mode == "covered-all" ? new Rectangle(40, 60, 300, 110) : new Rectangle(140, 80, 100, 60);
            if (Id == 3)
                return new Rectangle(40, 205, 300, 45);
            return Id == 4 ? new Rectangle(0, 0, 460, 270) : new Rectangle(40, 60, 300, 110);
        }
    }

    public ProviderOptions ProviderOptions
    {
        get
        {
            return ProviderOptions.ServerSideProvider;
        }
    }

    public IRawElementProviderSimple HostRawElementProvider
    {
        get
        {
            return null;
        }
    }

    public object GetPatternProvider(int id)
    {
        return Id == 3 && id == InvokePatternIdentifiers.Pattern.Id ? this : null;
    }

    public object GetPropertyValue(int id)
    {
        if (id == AutomationElementIdentifiers.ControlTypeProperty.Id)
            return Id == 3 ? ControlType.ListItem.Id : Id == 4 ? ControlType.Document.Id : ControlType.Button.Id;
        if (id == AutomationElementIdentifiers.NameProperty.Id)
            return Name;
        if (id == AutomationElementIdentifiers.AutomationIdProperty.Id)
            return "click-hit-" + Id;
        if (id == AutomationElementIdentifiers.IsEnabledProperty.Id || id == AutomationElementIdentifiers.IsControlElementProperty.Id || id == AutomationElementIdentifiers.IsContentElementProperty.Id)
            return true;
        if (id == AutomationElementIdentifiers.IsOffscreenProperty.Id || id == AutomationElementIdentifiers.IsKeyboardFocusableProperty.Id)
            return false;
        if (id == AutomationElementIdentifiers.ProcessIdProperty.Id)
            return Process.GetCurrentProcess().Id;
        if (id == AutomationElementIdentifiers.ClickablePointProperty.Id)
        {
            var b = BoundingRectangle;
            return new double[]
            {
                b.X + b.Width / 2,
                b.Y + b.Height / 2
            };
        }

        return null;
    }

    public Rect BoundingRectangle
    {
        get
        {
            var b = Root.Surface.RectangleToScreen(Local);
            return new Rect(b.X, b.Y, b.Width, b.Height);
        }
    }

    public IRawElementProviderFragmentRoot FragmentRoot
    {
        get
        {
            return Root;
        }
    }

    public IRawElementProviderFragment Navigate(NavigateDirection d)
    {
        if (d == NavigateDirection.Parent)
            return Root;
        if (d == NavigateDirection.NextSibling && Id < Root.Nodes.Length)
            return Root.Nodes[Id];
        if (d == NavigateDirection.PreviousSibling && Id > 1)
            return Root.Nodes[Id - 2];
        return null;
    }

    public int[] GetRuntimeId()
    {
        return new[]
        {
            AutomationInteropProvider.AppendRuntimeId,
            Id
        };
    }

    public IRawElementProviderSimple[] GetEmbeddedFragmentRoots()
    {
        return null;
    }

    public void SetFocus()
    {
    }

    public void Invoke()
    {
        ClickHitProbe.Mouse("invoke", "row");
    }
}

static class ClickHitProbe
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")]
    static extern bool AllowSetForegroundWindow(uint pid);
    [DllImport("user32.dll")]
    static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")]
    static extern bool SetProcessDPIAware();
    internal static string Mode = "plain";
    internal static bool Covered
    {
        get
        {
            return Mode == "covered" || Mode == "covered-all";
        }
    }

    static readonly Dictionary<string, int> Counts = new Dictionary<string, int>();
    static readonly List<object> Events = new List<object>();
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static string Folder;
    static ClickHitForm Form;
    static ClickHitSurface Surface;
    static ComboBox Combo;
    static int Command;
    internal static string CountSummary()
    {
        return Json.Serialize(Counts);
    }

    internal static void Mouse(string kind, string target)
    {
        string key = target + "." + kind;
        Counts[key] = Counts.ContainsKey(key) ? Counts[key] + 1 : 1;
        Events.Add(new { kind = kind, target = target, foreground = GetForegroundWindow().ToInt64(), mode = Mode });
        Surface.Invalidate();
        Save();
    }

    static void Save()
    {
        var data = new
        {
            pid = Process.GetCurrentProcess().Id,
            hwnd = Form.Handle.ToInt64(),
            surfaceHwnd = Surface.Handle.ToInt64(),
            foreground = GetForegroundWindow().ToInt64(),
            mode = Mode,
            command = Command,
            counts = Counts,
            events = Events,
            leftDown = (GetAsyncKeyState(1) & 0x8000) != 0,
            comboExpanded = Combo.DroppedDown,
            targetBounds = new double[]
            {
                Surface.Provider.Nodes[0].BoundingRectangle.X,
                Surface.Provider.Nodes[0].BoundingRectangle.Y,
                Surface.Provider.Nodes[0].BoundingRectangle.Width,
                Surface.Provider.Nodes[0].BoundingRectangle.Height
            }
        };
        string path = Path.Combine(Folder, "state.json");
        File.WriteAllText(path + ".tmp", Json.Serialize(data));
        try
        {
            if (File.Exists(path))
                File.Replace(path + ".tmp", path, null);
            else
                File.Move(path + ".tmp", path);
        }
        catch (IOException)
        {
            File.Delete(path + ".tmp");
        }
    }

    [STAThread]
    static void Main(string[] args)
    {
        SetProcessDPIAware();
        Folder = args[0];
        Directory.CreateDirectory(Folder);
        Application.EnableVisualStyles();
        Form = new ClickHitForm
        {
            Text = "Click hit boundary fixture",
            ClientSize = new System.Drawing.Size(560, 420),
            StartPosition = FormStartPosition.Manual,
            Location = new System.Drawing.Point(120, 80)
        };
        Surface = new ClickHitSurface
        {
            Bounds = new Rectangle(0, 0, 560, 320)
        };
        Form.Controls.Add(Surface);
        Combo = new ComboBox
        {
            AccessibleName = "Disclosure combo",
            Bounds = new Rectangle(40, 350, 300, 40),
            DropDownStyle = ComboBoxStyle.DropDownList
        };
        Combo.Items.AddRange(new object[] { "First", "Second" });
        Combo.SelectedIndex = 0;
        Form.Controls.Add(Combo);
        Form.Show();
        Save();
        var deadline = DateTime.UtcNow.AddMinutes(12);
        var timer = new Timer
        {
            Interval = 40
        };
        timer.Tick += (s, e) =>
        {
            string path = Path.Combine(Folder, "command.json");
            if (File.Exists(path))
            {
                var c = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
                File.Delete(path);
                Command = Convert.ToInt32(c["id"]);
                string kind = (string)c["kind"];
                if (kind == "setup")
                {
                    Mode = (string)c["mode"];
                    Counts.Clear();
                    Events.Clear();
                    Surface.Invalidate();
                }

                if (kind == "activate")
                {
                    Form.Show();
                    AllowSetForegroundWindow(UInt32.MaxValue);
                    SetForegroundWindow(Form.Handle);
                }

                if (kind == "stop")
                {
                    Save();
                    Application.ExitThread();
                    return;
                }
            }

            Save();
            if (DateTime.UtcNow > deadline)
                Application.ExitThread();
        };
        timer.Start();
        Application.Run();
        timer.Dispose();
        Form.Dispose();
    }
}
