// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Text.RegularExpressions;

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
            bool system = String.Equals(nativeClass, "Edit", StringComparison.OrdinalIgnoreCase) &&
                          String.Equals(realClass, "Edit", StringComparison.OrdinalIgnoreCase) && framework == "Win32";
            bool forms =
                WinFormsEdit.IsMatch(nativeClass) && framework == "WinForm" &&
                (String.Equals(realClass, "Edit", StringComparison.OrdinalIgnoreCase) || realClass == nativeClass);
            return system || forms ? new NativeSelectionInsertMechanism(nativeClass, realClass, framework) : null;
        }
        internal bool Same(NativeSelectionInsertMechanism current)
        {
            return current != null && NativeClass == current.NativeClass && RealClass == current.RealClass &&
                   Framework == current.Framework;
        }
    }
}
