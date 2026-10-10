// Enregistre UNIQUEMENT le son d'un processus (et de ses enfants) via le "process loopback" WASAPI (Windows 10 2004+).
// Usage : gameaudio.exe <pid> <sortie.wav>   -> s'arrête quand une ligne arrive sur l'entrée standard.
// Les paquets sont placés selon leur horodatage QPC : les silences (aucun paquet) deviennent des zéros, et
// <sortie.wav>.json donne l'instant (secondes QPC, même base que time.perf_counter de Python) du 1er échantillon.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

class GameAudio
{
    const int RATE = 48000, CH = 2, BITS = 32;  // flottant 32 bits : le volume du jeu peut être bas, on normalise au montage

    [StructLayout(LayoutKind.Sequential)]
    struct WAVEFORMATEX { public ushort wFormatTag, nChannels; public uint nSamplesPerSec, nAvgBytesPerSec; public ushort nBlockAlign, wBitsPerSample, cbSize; }

    [StructLayout(LayoutKind.Sequential)]
    struct ACTIVATION_PARAMS { public int ActivationType; public uint TargetProcessId; public int ProcessLoopbackMode; }

    [StructLayout(LayoutKind.Sequential)]
    struct PROPVARIANT_BLOB { public ushort vt, r1, r2, r3; public uint cbSize; public IntPtr pBlobData; }

    [ComImport, Guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioClient
    {
        int Initialize(int shareMode, uint flags, long bufferDuration, long periodicity, ref WAVEFORMATEX fmt, IntPtr session);
        int GetBufferSize(out uint frames);
        int GetStreamLatency(out long latency);
        int GetCurrentPadding(out uint padding);
        int IsFormatSupported(int shareMode, IntPtr fmt, out IntPtr closest);
        int GetMixFormat(out IntPtr fmt);
        int GetDevicePeriod(out long def, out long min);
        int Start();
        int Stop();
        int Reset();
        int SetEventHandle(IntPtr h);
        int GetService(ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object svc);
    }

    [ComImport, Guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAudioCaptureClient
    {
        int GetBuffer(out IntPtr data, out uint frames, out uint flags, out ulong devPos, out ulong qpcPos);
        int ReleaseBuffer(uint frames);
        int GetNextPacketSize(out uint frames);
    }

    [ComImport, Guid("72A22D78-CDE4-431D-B8CC-843A71199B6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceAsyncOperation
    {
        void GetActivateResult(out int hr, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
    }

    [ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IActivateAudioInterfaceCompletionHandler
    {
        void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op);
    }

    // IAgileObject : l'appel de retour arrive sur un autre thread
    [ComImport, Guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAgileObject { }

    class Handler : IActivateAudioInterfaceCompletionHandler, IAgileObject
    {
        public readonly ManualResetEvent Done = new ManualResetEvent(false);
        public object Client; public int Hr;
        public void ActivateCompleted(IActivateAudioInterfaceAsyncOperation op)
        {
            op.GetActivateResult(out Hr, out Client);
            Done.Set();
        }
    }

    [DllImport("Mmdevapi.dll", ExactSpelling = true, PreserveSig = false)]
    static extern void ActivateAudioInterfaceAsync([MarshalAs(UnmanagedType.LPWStr)] string path, ref Guid iid,
        IntPtr activationParams, IActivateAudioInterfaceCompletionHandler handler, out IActivateAudioInterfaceAsyncOperation op);

    static void Check(int hr, string what) { if (hr < 0) throw new Exception(what + " hr=0x" + hr.ToString("X8")); }

    [MTAThread]
    static int Main(string[] args)
    {
        if (args.Length < 2) { Console.Error.WriteLine("usage: gameaudio <pid> <out.wav>"); return 2; }
        uint pid = uint.Parse(args[0]);
        string outPath = args[1];

        var ap = new ACTIVATION_PARAMS { ActivationType = 1, TargetProcessId = pid, ProcessLoopbackMode = 0 };
        IntPtr apPtr = Marshal.AllocHGlobal(Marshal.SizeOf(ap));
        Marshal.StructureToPtr(ap, apPtr, false);
        var pv = new PROPVARIANT_BLOB { vt = 65 /* VT_BLOB */, cbSize = (uint)Marshal.SizeOf(ap), pBlobData = apPtr };
        IntPtr pvPtr = Marshal.AllocHGlobal(Marshal.SizeOf(pv));
        Marshal.StructureToPtr(pv, pvPtr, false);

        var handler = new Handler();
        Guid iidClient = typeof(IAudioClient).GUID;
        IActivateAudioInterfaceAsyncOperation op;
        ActivateAudioInterfaceAsync("VAD\\Process_Loopback", ref iidClient, pvPtr, handler, out op);
        if (!handler.Done.WaitOne(10000)) throw new Exception("activation timeout");
        Check(handler.Hr, "activate");
        var client = (IAudioClient)handler.Client;

        var fmt = new WAVEFORMATEX { wFormatTag = 3 /* IEEE float */, nChannels = CH, nSamplesPerSec = RATE, wBitsPerSample = BITS,
            nBlockAlign = CH * BITS / 8, nAvgBytesPerSec = RATE * CH * BITS / 8, cbSize = 0 };
        const uint LOOPBACK = 0x00020000, AUTOCONVERT = 0x80000000, SRC_DEFAULT = 0x08000000;
        Check(client.Initialize(0, LOOPBACK | AUTOCONVERT | SRC_DEFAULT, 10000000, 0, ref fmt, IntPtr.Zero), "initialize");
        Guid iidCap = typeof(IAudioCaptureClient).GUID;
        object capObj;
        Check(client.GetService(ref iidCap, out capObj), "getservice");
        var cap = (IAudioCaptureClient)capObj;

        var fs = new FileStream(outPath, FileMode.Create, FileAccess.Write);
        var bw = new BinaryWriter(fs);
        bw.Write(new byte[44]);  // en-tête écrit à la fin
        int block = CH * BITS / 8;
        long written = 0;  // échantillons (par canal) écrits
        double t0 = -1;    // secondes QPC du 1er échantillon
        bool stop = false;
        new Thread(() => { Console.In.ReadLine(); stop = true; }) { IsBackground = true }.Start();

        long freq; QueryPerformanceFrequency(out freq);
        Check(client.Start(), "start");
        Console.WriteLine("ready"); Console.Out.Flush();
        byte[] buf = new byte[0];
        while (!stop)
        {
            uint n;
            Check(cap.GetNextPacketSize(out n), "nextpacket");
            if (n == 0) { Thread.Sleep(5); continue; }
            IntPtr data; uint frames, flags; ulong dev, qpc;
            Check(cap.GetBuffer(out data, out frames, out flags, out dev, out qpc), "getbuffer");
            double t = qpc / 1e7;
            if (t0 < 0) t0 = t;
            long pos = (long)Math.Round((t - t0) * RATE);
            if (pos > written + RATE / 100)  // trou de plus de 10 ms : silence (le jeu n'émettait rien)
            {
                bw.Write(new byte[(pos - written) * block]);
                written = pos;
            }
            int bytes = (int)frames * block;
            if (buf.Length < bytes) buf = new byte[bytes];
            if ((flags & 2) != 0) Array.Clear(buf, 0, bytes);  // AUDCLNT_BUFFERFLAGS_SILENT
            else Marshal.Copy(data, buf, 0, bytes);
            bw.Write(buf, 0, bytes);
            written += frames;
            cap.ReleaseBuffer(frames);
        }
        client.Stop();
        // complète jusqu'à l'instant d'arrêt (silence final)
        long now; QueryPerformanceCounter(out now);
        if (t0 < 0) t0 = (double)now / freq;
        long endPos = (long)Math.Round(((double)now / freq - t0) * RATE);
        if (endPos > written) { bw.Write(new byte[(endPos - written) * block]); written = endPos; }

        long dataBytes = written * block;
        fs.Seek(0, SeekOrigin.Begin);
        bw.Write(new[] { (byte)'R', (byte)'I', (byte)'F', (byte)'F' }); bw.Write((uint)(36 + dataBytes));
        bw.Write(new[] { (byte)'W', (byte)'A', (byte)'V', (byte)'E', (byte)'f', (byte)'m', (byte)'t', (byte)' ' });
        bw.Write(16u); bw.Write((ushort)3); bw.Write((ushort)CH); bw.Write((uint)RATE); bw.Write((uint)(RATE * block));
        bw.Write((ushort)block); bw.Write((ushort)BITS);
        bw.Write(new[] { (byte)'d', (byte)'a', (byte)'t', (byte)'a' }); bw.Write((uint)dataBytes);
        bw.Close();
        File.WriteAllText(outPath + ".json", "{\"t0\": " + t0.ToString("R", System.Globalization.CultureInfo.InvariantCulture)
            + ", \"rate\": " + RATE + ", \"samples\": " + written + "}");
        return 0;
    }

    [DllImport("kernel32.dll")] static extern bool QueryPerformanceFrequency(out long f);
    [DllImport("kernel32.dll")] static extern bool QueryPerformanceCounter(out long c);
}
