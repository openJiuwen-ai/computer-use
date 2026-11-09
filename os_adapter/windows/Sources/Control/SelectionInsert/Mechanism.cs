// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Text.RegularExpressions;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // PURE mechanism evidence for selection editing, independent of WM_CHAR
    // eligibility. It does not assert absence of application message handlers.
    internal sealed class NativeSelectionInsertMechanism
    {
        internal readonly string NativeClass, RealClass, Framework;
        static readonly Regex WinFormsEdit =
            new Regex(@"\AWindowsForms10\.EDIT\.app\.[0-9]+\.[0-9a-f]+_r[0-9]+_ad[0-9]+\z",
                      RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        NativeSelectionInsertMechanism(string nativeClass, string realClass, string framework)
        {
            NativeClass = nativeClass;
            RealClass = realClass;
            Framework = framework;
        }
        internal static NativeSelectionInsertMechanism Resolve(string nativeClass, string realClass, string framework,
                                                               bool unicode, long style, string uiaClass)
        {
            // Known formatting/password/multiline mechanisms retain the old
            // routes. Do not choose different input semantics by payload length.
            if (!unicode || String.IsNullOrEmpty(nativeClass) || String.IsNullOrEmpty(realClass) ||
                (style & (4 | 8 | 16 | 32 | 0x400 | 0x800 | 0x2000)) != 0 ||
                !String.IsNullOrEmpty(uiaClass) &&
                    uiaClass.IndexOf("MaskedTextBox", StringComparison.OrdinalIgnoreCase) >= 0)
                return null;
            return framework != null && framework == NativeFramework(nativeClass, realClass)
                       ? new NativeSelectionInsertMechanism(nativeClass, realClass, framework)
                       : null;
        }
        internal static string NativeFramework(string nativeClass, string realClass)
        {
            bool realEdit = String.Equals(realClass, "Edit", StringComparison.OrdinalIgnoreCase);
            if (realEdit && String.Equals(nativeClass, "Edit", StringComparison.OrdinalIgnoreCase))
                return "Win32";
            return nativeClass != null && WinFormsEdit.IsMatch(nativeClass) && (realEdit || realClass == nativeClass)
                       ? "WinForm"
                       : null;
        }
        internal bool Same(NativeSelectionInsertMechanism current)
        {
            return current != null && NativeClass == current.NativeClass && RealClass == current.RealClass &&
                   Framework == current.Framework;
        }
        internal static NativeSelectionInsertMechanism ResolveReplacement(string nativeClass, string realClass,
                                                                          string framework, bool unicode, long style,
                                                                          string uiaClass)
        {
            const long multiline = 0x4;
            return (style & multiline) == 0 ? null
                                            : Resolve(nativeClass, realClass, framework, unicode, style & ~multiline, uiaClass);
        }
        internal static bool IsReplacementRole(ControlType role)
        {
            return role == ControlType.Edit || role == ControlType.Document;
        }
    }
}
