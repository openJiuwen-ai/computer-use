using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeObjectState
    {
        internal object Native;
        internal string Role, Label;
        internal string ClickMetadataUnavailable;
        internal string TextValue;
        internal bool TextValueKnown;
        internal bool? Enabled, Replaceable;
        internal bool ActionsComplete;
        internal string[] Actions = new string[0];
    }
}
