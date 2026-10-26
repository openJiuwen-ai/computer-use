using System;
using System.Text;
using UIA = NativeUIA;

namespace ComputerUse.Windows
{
    // Actual UIAutomationCore calls, without windows or provider/UI actions.
    internal static class NativeTransactionBudgetTests
    {
        static int assertions;
        static void Assert(bool condition, string detail)
        {
            assertions++;
            if (!condition)
                throw new InvalidOperationException(detail);
        }

        [MTAThread]
        static void Main()
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            var client = (UIA.IUIAutomation2)UiaClient.Current;
            uint original = client.TransactionTimeout;
            foreach (int requested in new[]
            {
                1,
                20,
                49,
                50,
                350
            }

            )
            {
                using (UiaClient.TransactionBudget(requested))
                {
                    Assert(client.TransactionTimeout >= 50 && client.TransactionTimeout <= 350, "The actual UIA timeout configuration is outside the supported slice range");
                }

                Assert(client.TransactionTimeout == original, "Disposal did not restore the original client timeout");
            }

            using (UiaClient.TransactionBudget(300))
            {
                using (UiaClient.TransactionBudget(1))
                    Assert(client.TransactionTimeout == 50, "Nested short slice was not supported by UIAutomationCore");
                Assert(client.TransactionTimeout == 300, "Nested disposal lost the enclosing client timeout");
            }

            Assert(client.TransactionTimeout == original, "Nested disposal did not restore the original timeout");
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "scope", "actual UIAutomationCore timeout configuration and restoration; no provider or GUI claims")));
        }
    }
}
