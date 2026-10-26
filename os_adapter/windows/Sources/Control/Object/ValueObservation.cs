using System;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // Optional UIA value evidence is separate from action/target authority.
    // Only provider COM failures become unknown; request failures propagate.
    internal static class NativeObjectValueObservation
    {
        internal static object Property(Func<object> read, Request r)
        {
            r.Check();
            try
            {
                var value = read();
                r.Check();
                return value;
            }
            catch (COMException)
            {
                r.Check();
                return null;
            }
        }

        internal static void Read(NativeObjectState state, Func<object> readOnly, Func<object> password, Func<object> value, Request r)
        {
            // Unknown writability disables replacement, not another declared
            // action. Unknown privacy/value leaves effect confirmation unknown.
            state.Replaceable = !(Property(readOnly, r) as bool? );
            if (Property(password, r) as bool? != false)
                return;
            var observed = Property(value, r) as string;
            state.TextValue = observed;
            state.TextValueKnown = observed != null;
        }
    }
}
