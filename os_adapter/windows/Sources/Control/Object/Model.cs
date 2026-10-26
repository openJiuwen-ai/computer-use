using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class ObjectDispatchPlan
    {
        internal readonly NativeInputContract Contract;
        internal readonly Dictionary<string, object> Target;
        internal readonly string Unit, Text;
        internal ObjectDispatchPlan(NativeInputContract contract, Dictionary<string, object> target, string unit, string text)
        {
            Contract = contract;
            Target = target;
            Unit = unit;
            Text = text;
        }
    }
}
