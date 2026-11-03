// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    // Session authority for the global input channel, independent of window
    // activation and object-directed UIA/message operations.
    internal interface INativeGlobalKeyboardAuthority
    {
        void RequireGlobalInput();
    }

    internal static class NativeGlobalInput
    {
        internal static void Require(bool allowed)
        {
            if (!allowed)
                throw new Failure("permission_required", "this route requires global mouse or keyboard input",
                                  "request explicit global input authorization before using this route");
        }
    }
}
