// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Automation;

internal static class WindowsCalculatorOracle
{
    [STAThread]
    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        var pid = new PropertyCondition(AutomationElement.ProcessIdProperty, Int32.Parse(args[0]));
        var results = new OrCondition(
            new PropertyCondition(AutomationElement.AutomationIdProperty, "CalculatorResults"),
            new PropertyCondition(AutomationElement.AutomationIdProperty, "CalculatorAlwaysOnTopResults"));
        var result = AutomationElement.RootElement.FindFirst(TreeScope.Descendants, new AndCondition(pid, results));
        if (result == null)
            return 2;
        var expression = AutomationElement.RootElement.FindFirst(
            TreeScope.Descendants,
            new AndCondition(pid, new PropertyCondition(AutomationElement.AutomationIdProperty, "CalculatorExpression")));
        Console.WriteLine(new JavaScriptSerializer().Serialize(new Dictionary<string, object>
        {
            { "result", result.Current.Name },
            { "expression", expression == null ? null : expression.Current.Name }
        }));
        return 0;
    }
}
