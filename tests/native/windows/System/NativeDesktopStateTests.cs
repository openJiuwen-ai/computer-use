using System;

namespace ComputerUse.Windows
{
    internal static class NativeDesktopStateTests
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static void Allowed(NativeDesktopState.State state)
        {
            state.RequireInput();
            assertions++;
        }

        static void Refused(NativeDesktopState.State state)
        {
            try
            {
                state.RequireInput();
                throw new InvalidOperationException("closed lid and off display allowed physical input");
            }
            catch (Failure error)
            {
                Assert(error.Code == "input_unavailable", "wrong failure category");
                Assert(error.Message.Contains("no requested physical input sent"), "dispatch boundary missing");
                Assert(error.Next.Contains("active session display"), "actionable next step missing");
            }
        }

        static void Main()
        {
            var state = new NativeDesktopState.State();
            Allowed(state); // Unknown is not a known unavailable desktop.
            state.Update(NativeDesktopState.LidSwitch, 4, 0);
            Allowed(state);
            state.Update(NativeDesktopState.SessionDisplay, 4, 1);
            Allowed(state); // External display, closed lid.
            state.Update(NativeDesktopState.SessionDisplay, 4, 2);
            Allowed(state); // Dimmed display.
            state.Update(NativeDesktopState.SessionDisplay, 4, 0);
            Refused(state);
            state.Update(NativeDesktopState.LidSwitch, 4, 1);
            Allowed(state); // Open lid can wake on input.
            state.Update(NativeDesktopState.LidSwitch, 4, 0);
            Refused(state);
            state.Update(Guid.Empty, 4, 1);
            Refused(state);
            state.Update(NativeDesktopState.SessionDisplay, 8, 1);
            Refused(state);
            state.Update(NativeDesktopState.SessionDisplay, 4, 42);
            Refused(state);
            state.Clear();
            Allowed(state); // A dead observation source cannot keep denying input.
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "scope", "notification policy only; real desktop acceptance is separate")));
        }
    }
}
