// Disposable WPF provider with deliberately distinct Document/Text and Value.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Automation.Text;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Interop;
using System.Windows.Threading;

class ReadingLayout : StackPanel
{
    protected override AutomationPeer OnCreateAutomationPeer()
    {
        return new ReadingLayoutPeer(this);
    }
}

class ReadingLayoutPeer : FrameworkElementAutomationPeer
{
    internal ReadingLayoutPeer(ReadingLayout owner) : base(owner)
    {
    }

    protected override string GetNameCore()
    {
        return "";
    }

    protected override string GetClassNameCore()
    {
        return "ReadingLayout";
    }

    protected override AutomationControlType GetAutomationControlTypeCore()
    {
        return AutomationControlType.Group;
    }

    protected override bool IsControlElementCore()
    {
        return false;
    }

    protected override bool IsContentElementCore()
    {
        return false;
    }
}

class ReadingBadge : TextBlock
{
    protected override AutomationPeer OnCreateAutomationPeer()
    {
        return new ReadingBadgePeer(this);
    }
}

class ReadingBadgePeer : TextBlockAutomationPeer
{
    internal ReadingBadgePeer(ReadingBadge owner) : base(owner)
    {
    }

    protected override bool IsControlElementCore()
    {
        return false;
    }

    protected override bool IsContentElementCore()
    {
        return false;
    }
}

class ReadingDocument : RichTextBox
{
    internal string ControlValue = "Control status 42";
    protected override AutomationPeer OnCreateAutomationPeer()
    {
        return new ReadingDocumentPeer(this);
    }
}

class ReadingDocumentPeer : RichTextBoxAutomationPeer, IValueProvider
{
    readonly ReadingDocument document;
    internal ReadingDocumentPeer(ReadingDocument owner) : base(owner)
    {
        document = owner;
    }

    public override object GetPattern(PatternInterface pattern)
    {
        if (pattern == PatternInterface.Value)
            return this;
        object value = base.GetPattern(pattern);
        return pattern == PatternInterface.Text && value != null ? new PassiveText((ITextProvider)value) : value;
    }

    bool IValueProvider.IsReadOnly
    {
        get
        {
            return true;
        }
    }

    string IValueProvider.Value
    {
        get
        {
            return document.ControlValue;
        }
    }

    void IValueProvider.SetValue(string value)
    {
        ReadingProbe.Mutations++;
        throw new InvalidOperationException("Read changed Value");
    }
}

class PassiveText : ITextProvider
{
    readonly ITextProvider inner;
    internal PassiveText(ITextProvider value)
    {
        inner = value;
    }

    internal static ITextRangeProvider Wrap(ITextRangeProvider value)
    {
        return value == null ? null : new PassiveRange(value);
    }

    static ITextRangeProvider[] WrapAll(ITextRangeProvider[] values)
    {
        return Array.ConvertAll(values, Wrap);
    }

    public ITextRangeProvider DocumentRange
    {
        get
        {
            return Wrap(inner.DocumentRange);
        }
    }

    public SupportedTextSelection SupportedTextSelection
    {
        get
        {
            return inner.SupportedTextSelection;
        }
    }

    public ITextRangeProvider[] GetSelection()
    {
        return WrapAll(inner.GetSelection());
    }

    public ITextRangeProvider[] GetVisibleRanges()
    {
        return WrapAll(inner.GetVisibleRanges());
    }

    public ITextRangeProvider RangeFromChild(IRawElementProviderSimple child)
    {
        return Wrap(inner.RangeFromChild(child));
    }

    public ITextRangeProvider RangeFromPoint(Point point)
    {
        return Wrap(inner.RangeFromPoint(point));
    }
}

class PassiveRange : ITextRangeProvider
{
    readonly ITextRangeProvider inner;
    internal PassiveRange(ITextRangeProvider value)
    {
        inner = value;
    }

    static ITextRangeProvider Unwrap(ITextRangeProvider value)
    {
        var wrapped = value as PassiveRange;
        return wrapped == null ? value : wrapped.inner;
    }

    public ITextRangeProvider Clone()
    {
        return PassiveText.Wrap(inner.Clone());
    }

    public bool Compare(ITextRangeProvider other)
    {
        return inner.Compare(Unwrap(other));
    }

    public int CompareEndpoints(TextPatternRangeEndpoint endpoint, ITextRangeProvider target, TextPatternRangeEndpoint targetEndpoint)
    {
        return inner.CompareEndpoints(endpoint, Unwrap(target), targetEndpoint);
    }

    public void ExpandToEnclosingUnit(TextUnit unit)
    {
        inner.ExpandToEnclosingUnit(unit);
    }

    public ITextRangeProvider FindAttribute(int attribute, object value, bool backward)
    {
        return PassiveText.Wrap(inner.FindAttribute(attribute, value, backward));
    }

    public ITextRangeProvider FindText(string text, bool backward, bool ignoreCase)
    {
        return PassiveText.Wrap(inner.FindText(text, backward, ignoreCase));
    }

    public object GetAttributeValue(int attribute)
    {
        return inner.GetAttributeValue(attribute);
    }

    public double[] GetBoundingRectangles()
    {
        return inner.GetBoundingRectangles();
    }

    public IRawElementProviderSimple GetEnclosingElement()
    {
        return inner.GetEnclosingElement();
    }

    public string GetText(int maxLength)
    {
        return inner.GetText(maxLength);
    }

    public int Move(TextUnit unit, int count)
    {
        return inner.Move(unit, count);
    }

    public void MoveEndpointByRange(TextPatternRangeEndpoint endpoint, ITextRangeProvider target, TextPatternRangeEndpoint targetEndpoint)
    {
        inner.MoveEndpointByRange(endpoint, Unwrap(target), targetEndpoint);
    }

    public int MoveEndpointByUnit(TextPatternRangeEndpoint endpoint, TextUnit unit, int count)
    {
        return inner.MoveEndpointByUnit(endpoint, unit, count);
    }

    public IRawElementProviderSimple[] GetChildren()
    {
        return inner.GetChildren();
    }

    static void RejectMutation()
    {
        ReadingProbe.Mutations++;
        throw new InvalidOperationException("Read changed selection or scroll");
    }

    public void Select()
    {
        RejectMutation();
    }

    public void AddToSelection()
    {
        RejectMutation();
    }

    public void RemoveFromSelection()
    {
        RejectMutation();
    }

    public void ScrollIntoView(bool alignToTop)
    {
        RejectMutation();
    }
}

static class ReadingProbe
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    internal static int Mutations;
    [STAThread]
    static void Main(string[] args)
    {
        string output = args[0];
        Directory.CreateDirectory(output);
        var window = new Window
        {
            Title = "Windows Reading Probe",
            Width = 760,
            Height = 620,
            ShowActivated = false
        };
        var layout = new ReadingLayout
        {
            Margin = new Thickness(16)
        };
        var nested = new ReadingLayout();
        window.Content = layout;
        layout.Children.Add(nested);
        nested.Children.Add(new ReadingBadge { Text = "Raw-only status: 42", Height = 24 });
        var input = new TextBox
        {
            Text = "Editable value 73",
            Height = 28
        };
        AutomationProperties.SetName(input, "Ordinary input");
        AutomationProperties.SetAutomationId(input, "ordinaryInput");
        nested.Children.Add(input);
        var document = new ReadingDocument
        {
            Height = 360,
            IsReadOnly = true,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        };
        AutomationProperties.SetName(document, "Document body");
        AutomationProperties.SetAutomationId(document, "body");
        var body = new StringBuilder("READING-BEGIN 正文来源验证🙂\n");
        for (int i = 0; i < 180; i++)
            body.Append("段落 ").Append(i.ToString("D3")).Append("：相同文字 beta beta；完整正文与控件状态分别保留。 Unicode 🙂 e\u0301\n");
        body.Append("READING-END 完整正文结束");
        var paragraph = new Paragraph(new Run(body.ToString()));
        document.Document = new FlowDocument(paragraph);
        nested.Children.Add(document);
        var action = new Button
        {
            Content = "Independent action",
            Height = 28
        };
        AutomationProperties.SetAutomationId(action, "independentAction");
        action.Click += delegate
        {
            Mutations++;
        };
        nested.Children.Add(action);
        var toggle = new CheckBox
        {
            Content = "Keep this checked",
            IsChecked = true
        };
        AutomationProperties.SetAutomationId(toggle, "checkedState");
        nested.Children.Add(toggle);
        var serializer = new JavaScriptSerializer
        {
            MaxJsonLength = 1024 * 1024
        };
        string selected = null;
        double scroll = 0;
        bool ready = false;
        DateTime deadline = DateTime.UtcNow.AddMinutes(12);
        var timer = new DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(250)
        };
        timer.Tick += delegate
        {
            if (!ready)
            {
                document.Selection.Select(paragraph.ContentStart, paragraph.ContentStart.GetPositionAtOffset(13));
                input.Select(2, 5);
                selected = document.Selection.Text;
                scroll = document.VerticalOffset;
                ready = true;
            }

            document.ControlValue = File.Exists(Path.Combine(output, "empty-value")) ? "" : "Control status 42";
            var truth = new Dictionary<string, object>
            {
                {
                    "pid",
                    Process.GetCurrentProcess().Id
                },
                {
                    "hwnd",
                    new WindowInteropHelper(window).Handle.ToInt64()
                },
                {
                    "body",
                    body.ToString()
                },
                {
                    "controlValue",
                    document.ControlValue
                },
                {
                    "input",
                    input.Text
                },
                {
                    "inputSelection",
                    input.SelectedText
                },
                {
                    "documentSelection",
                    document.Selection.Text
                },
                {
                    "initialSelection",
                    selected
                },
                {
                    "scroll",
                    document.VerticalOffset
                },
                {
                    "initialScroll",
                    scroll
                },
                {
                    "checked",
                    toggle.IsChecked
                },
                {
                    "mutations",
                    Mutations
                },
                {
                    "foreground",
                    GetForegroundWindow().ToInt64()
                }
            };
            File.WriteAllText(Path.Combine(output, "truth.json"), serializer.Serialize(truth));
            if (DateTime.UtcNow > deadline || File.Exists(Path.Combine(output, "stop")))
                window.Close();
        };
        window.Closed += delegate
        {
            timer.Stop();
        };
        timer.Start();
        new Application().Run(window);
    }
}
