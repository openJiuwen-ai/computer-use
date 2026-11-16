// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Xml;

namespace ComputerUse.Windows
{
    internal sealed class NativeApplicationEntry
    {
        internal readonly string Name, ShellId, Executable;

        internal NativeApplicationEntry(string name, string shellId, string executable)
        {
            Name = name;
            ShellId = shellId;
            Executable = executable;
        }

        internal bool Same(NativeApplicationEntry entry)
        {
            return NativeApplicationShell.Same(ShellId, entry.ShellId) &&
                   NativeApplicationShell.Same(Executable, entry.Executable);
        }
    }

    // AppsFolder supplies launch entries for the signed-in user.
    // The desktop Shell owns the resulting app, outside the driver's kill-on-close jobs.
    internal static class NativeApplicationShell
    {
        const string AppsFolder = "shell:AppsFolder";

        internal static bool Same(string left, string right)
        {
            return String.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }

        internal static string ExecutablePath(string path)
        {
            if (String.IsNullOrEmpty(path) || path.Length < 3 ||
                !(path[1] == ':' && (path[2] == '\\' || path[2] == '/') || path.StartsWith(@"\\")))
                return null;
            try
            {
                var expanded = Path.GetFullPath(path);
                return Same(Path.GetExtension(expanded), ".exe") ? expanded : null;
            }
            catch (ArgumentException) { return null; }
            catch (NotSupportedException) { return null; }
            catch (PathTooLongException) { return null; }
        }

        static object Shell()
        {
            return Activator.CreateInstance(Type.GetTypeFromProgID("Shell.Application", true));
        }

        static void Release(object value)
        {
            if (value != null && Marshal.IsComObject(value))
                Marshal.ReleaseComObject(value);
        }

        static void CheckLookup(Request request)
        {
            request.Check();
            if (Wire.Now >= request.ObservationDeadline)
                throw new Failure("observation_incomplete", "Windows launch entry lookup exceeded its observation budget");
        }

        static NativeApplicationEntry Entry(dynamic item, string name, string id)
        {
            return new NativeApplicationEntry(name, id,
                ExecutablePath(OptionalProperty(item, "System.Link.TargetParsingPath")));
        }

        static string OptionalProperty(dynamic item, string key)
        {
            try { return item.ExtendedProperty(key) as string; }
            catch (COMException) { return null; }
        }

        static NativeApplicationEntry Exact(dynamic folder, string id)
        {
            object raw = folder.ParseName(id);
            try
            {
                dynamic item = raw;
                if (raw == null || !Same((string)item.Path, id))
                    return null;
                string name = item.Name;
                return Entry(raw, String.IsNullOrWhiteSpace(name) ? id : name, id);
            }
            finally
            {
                Release(raw);
            }
        }

        static bool ExecutableName(string executable, string selector)
        {
            if (String.IsNullOrEmpty(executable))
                return false;
            string name = Path.GetFileName(executable);
            return Same(Path.GetExtension(name), ".exe") &&
                   (Same(name, selector) || Same(Path.GetFileNameWithoutExtension(name), selector));
        }

        static XmlDocument ReadPackageManifest(string path)
        {
            try
            {
                var manifest = new XmlDocument { XmlResolver = null };
                using (var reader = XmlReader.Create(Path.Combine(path, "AppxManifest.xml"),
                    new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit }))
                    manifest.Load(reader);
                return manifest;
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            catch (XmlException) { }
            catch (ArgumentException) { }
            return null;
        }

        static bool PackageAlias(XmlDocument manifest, string id, string selector)
        {
            string application = id.Substring(id.IndexOf('!') + 1);
            foreach (XmlElement app in manifest.SelectNodes(
                "/*[local-name()='Package']/*[local-name()='Applications']/*[local-name()='Application']"))
            {
                if (!Same(app.GetAttribute("Id"), application))
                    continue;
                return ExecutableName(app.GetAttribute("Executable"), selector) ||
                       app.SelectNodes(".//*[local-name()='ExecutionAlias']").Cast<XmlElement>()
                          .Any(alias => ExecutableName(alias.GetAttribute("Alias"), selector));
            }
            return false;
        }

        internal static NativeApplicationEntry[] FindAliases(
            Dictionary<NativeApplicationEntry, string> packages, string selector, Request request)
        {
            bool incomplete = false;
            var matches = new Dictionary<string, NativeApplicationEntry>(StringComparer.OrdinalIgnoreCase);
            foreach (var package in packages.GroupBy(item => item.Value, StringComparer.OrdinalIgnoreCase))
            {
                CheckLookup(request);
                var manifest = ReadPackageManifest(package.Key);
                if (manifest == null)
                {
                    incomplete = true;
                    continue;
                }
                foreach (var item in package)
                {
                    CheckLookup(request);
                    var entry = item.Key;
                    if (PackageAlias(manifest, entry.ShellId, selector))
                        matches[entry.ShellId] = entry;
                }
            }
            if (matches.Count == 0 && incomplete)
                throw new Failure("observation_incomplete",
                                  "Windows execution alias lookup could not read an installed package manifest. Use the Start menu name or application ID.");
            return matches.Values.ToArray();
        }

        internal static NativeApplicationEntry[] Find(string selector, Request request)
        {
            CheckLookup(request);
            string path = ExecutablePath(selector);
            if (path != null)
                return File.Exists(path) ? new[] { new NativeApplicationEntry(Path.GetFileNameWithoutExtension(path), null, path) }
                                         : new NativeApplicationEntry[0];

            object shell = null, folder = null, items = null;
            try
            {
                shell = Shell();
                folder = ((dynamic)shell).NameSpace(AppsFolder);
                if (folder == null)
                    throw new Failure("observation_unavailable", "Windows AppsFolder is unavailable in this session");
                NativeApplicationEntry exact = Exact((dynamic)folder, selector);
                if (exact != null)
                    return new[] { exact };

                var matches = new Dictionary<string, NativeApplicationEntry>(StringComparer.OrdinalIgnoreCase);
                var packages = new Dictionary<NativeApplicationEntry, string>();
                items = ((dynamic)folder).Items();
                foreach (object raw in (IEnumerable)items)
                {
                    try
                    {
                        CheckLookup(request);
                        dynamic item = raw;
                        string name = item.Name, id = item.Path;
                        if (String.IsNullOrWhiteSpace(id) || String.IsNullOrWhiteSpace(name))
                            continue;
                        var entry = Entry(raw, name, id);
                        if (Same(id, selector))
                            return new[] { entry };
                        if (Same(name, selector) || ExecutableName(entry.Executable, selector))
                            matches[id] = entry;
                        else if (matches.Count == 0 && id.IndexOf('!') >= 0)
                        {
                            string location = OptionalProperty(raw, "System.AppUserModel.PackageInstallPath");
                            if (!String.IsNullOrEmpty(location))
                                packages.Add(entry, location);
                        }
                    }
                    finally
                    {
                        Release(raw);
                    }
                }
                // Inspect each package only if no exact Shell entry or standalone executable name matched.
                var selected = matches.Count == 0 ? FindAliases(packages, selector, request) : matches.Values.ToArray();
                CheckLookup(request);
                return selected.OrderBy(entry => entry.Name).ThenBy(entry => entry.ShellId).ToArray();
            }
            catch (COMException error)
            {
                throw new Failure("observation_unavailable", "Windows launch entry lookup failed: " + error.Message);
            }
            finally
            {
                Release(items);
                Release(folder);
                Release(shell);
            }
        }

        internal static void Open(NativeApplicationEntry entry, Action sending)
        {
            InvokeOpen(markSending => OpenEntry(entry, markSending), sending);
        }

        internal static void InvokeOpen(Action<Action> invoke, Action sending)
        {
            bool attempted = false;
            try
            {
                invoke(() => {
                    sending();
                    attempted = true;
                });
            }
            catch (Failure) { throw; }
            catch (NativeDesktopCleanupFailure) { throw; }
            catch (Exception error)
            {
                string code = attempted ? "execution_unknown" : "input_unavailable";
                throw new Failure(code, "Windows desktop Shell startup failed: " + error.Message);
            }
        }

        static void OpenEntry(NativeApplicationEntry entry, Action sending)
        {
            object shell = null, windows = null, desktop = null, view = null, broker = null, folder = null;
            try
            {
                shell = Shell();
                string target;
                if (entry.ShellId == null)
                {
                    if (!File.Exists(entry.Executable))
                        throw new Failure("stale_target", "the selected Windows executable is no longer available");
                    target = entry.Executable;
                }
                else
                {
                    folder = ((dynamic)shell).NameSpace(AppsFolder);
                    var current = folder == null ? null : Exact(folder, entry.ShellId);
                    if (current == null || !entry.Same(current))
                        throw new Failure("stale_target", "the selected Windows launch entry changed");
                    target = AppsFolder + @"\" + entry.ShellId;
                }

                windows = ((dynamic)shell).Windows();
                int desktopHandle;
                desktop = ((dynamic)windows).FindWindowSW(0, 0, 8, out desktopHandle, 1); // SWC_DESKTOP, SWFO_NEEDDISPATCH
                if (desktop == null || desktopHandle == 0)
                    throw new Failure("input_unavailable", "the interactive Windows desktop Shell is unavailable");
                view = ((dynamic)desktop).Document;
                broker = ((dynamic)view).Application;
                sending();
                ((dynamic)broker).ShellExecute(target, "", "", "open", 1);
            }
            finally
            {
                Release(broker);
                Release(view);
                Release(desktop);
                Release(windows);
                Release(folder);
                Release(shell);
            }
        }
    }
}
