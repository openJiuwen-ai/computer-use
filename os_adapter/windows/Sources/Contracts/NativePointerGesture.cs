// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // Read-only projection of the retained native plan, not a public option or
    // a second attempt owner. Queries receive only the requested gesture.
    internal sealed class NativePointerGestureIntent
    {
        internal readonly string Kind;
        internal readonly uint Down;
        internal readonly int Count;
        internal NativePointerGestureIntent(string kind, uint down, int count)
        {
            Kind = kind;
            Down = down;
            Count = count;
        }
        internal bool CanActivateWindow
        {
            get {
                return Kind == "click" && Down == 2 && Count == 1;
            }
        }
    }
}
