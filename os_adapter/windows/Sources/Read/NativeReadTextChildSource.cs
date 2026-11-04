// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // QUERY-only exact range supplied for the selected TextChild. The nearest
    // container is queried for protection/identity, never for its document text,
    // selection, visible ranges or input capabilities.
    internal sealed class NativeReadTextChildSource
    {
        internal static bool RequiresOwnTextPattern(string field)
        {
            return field == "Text.SupportedTextSelection" || field == "Text.Selection" ||
                   field == "Text.SelectionBounds" || field == "Text.VisibleRanges" || field == "Text.ObjectRanges" ||
                   field == "Text.CaretRange";
        }
        readonly UiaTextChildPattern pattern;
        readonly UiaElement container;
        readonly Action check;
        internal readonly UiaTextRange Range;
        internal NativeReadTextChildSource(UiaElement target, Action check)
        {
            this.check = check;
            check();
            pattern = target.ReadTextChildPattern();
            check();
            container = pattern.TextContainer;
            check();
            if (container == null)
                throw new NativeFieldUnavailable("absent", "TextChildPattern returned no text container");
            RequireUnprotected(container);
            Range = pattern.TextRange;
            check();
            if (Range == null)
                throw new NativeFieldUnavailable("absent", "TextChildPattern returned no child text range");
        }
        void RequireUnprotected(UiaElement value)
        {
            check();
            if (value.GetCurrentPropertyValue(AutomationElement.IsPasswordProperty, true) as bool? != false)
                throw new NativeFieldUnavailable(
                    "unsupported",
                    "TextChild text-container protection is protected or unknown; its range is not captured");
            check();
        }
        internal void Validate()
        {
            check();
            var current = pattern.TextContainer;
            check();
            if (current == null ||
                UiaClient.Call(() => UiaClient.Current.CompareElements(container.Native, current.Native)) == 0)
                throw new NativeFieldUnavailable(
                    "error",
                    "TextChild text container changed during observation; the original range was not replaced");
            RequireUnprotected(current);
        }
    }
}
