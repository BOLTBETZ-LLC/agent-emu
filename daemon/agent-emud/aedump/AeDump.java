import android.app.UiAutomation;
import android.graphics.Point;
import android.system.Os;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import java.io.*;
import java.lang.reflect.Method;

/**
 * Persistent `uiautomator dump`: keeps one UiAutomation connection open and writes the same XML on demand,
 * without the new JVM and waitForIdle each `uiautomator dump` pays (2.4 s per call on an RN screen).
 * Uses the device's own uiautomator.jar classes (UiAutomationShellWrapper, AccessibilityNodeInfoDumper), so
 * the XML matches `uiautomator dump` byte for byte in format.
 *
 * Protocol (FIFOs in /data/local/tmp): write a line to ae.req; read "ok" (or "err ...") from ae.res; the
 * dump is in ae.xml. Run with CLASSPATH=<this dex>:/system/framework/uiautomator.jar via app_process.
 *
 * Build (JDK 17, SDK build-tools 36), in this dir, then bump DEX_PATH in src/uidump.rs:
 *   javac --release 11 -cp $SDK/platforms/android-36/android.jar -d out AeDump.java
 *   java -cp $SDK/build-tools/36.0.0/lib/d8.jar com.android.tools.r8.D8 --min-api 30
 *     --lib $SDK/platforms/android-36/android.jar --output . out/AeDump.class && mv classes.dex aedump.dex
 */
public class AeDump {
    static final String D = "/data/local/tmp/";

    public static void main(String[] args) throws Throwable {
        Class<?> w = Class.forName("com.android.uiautomator.core.UiAutomationShellWrapper");
        Object wrap = w.getConstructor().newInstance();
        w.getMethod("connect").invoke(wrap);
        // `uiautomator dump` compresses only with --compressed.
        w.getMethod("setCompressedLayoutHierarchy", boolean.class).invoke(wrap, false);
        UiAutomation ua = (UiAutomation) w.getMethod("getUiAutomation").invoke(wrap);
        Method dump = Class.forName("com.android.uiautomator.core.AccessibilityNodeInfoDumper").getMethod(
            "dumpWindowToFile", AccessibilityNodeInfo.class, File.class, int.class, int.class, int.class);
        Class<?> dmg = Class.forName("android.hardware.display.DisplayManagerGlobal");
        Object g = dmg.getMethod("getInstance").invoke(null);
        Method real = dmg.getMethod("getRealDisplay", int.class);

        new File(D + "ae.res").delete();
        new File(D + "ae.req").delete();
        Os.mkfifo(D + "ae.res", 0600);
        Os.mkfifo(D + "ae.req", 0600); // last: the daemon waits for this one
        while (true) {
            try (BufferedReader r = new BufferedReader(new FileReader(D + "ae.req"))) {
                if (r.readLine() == null) continue;
            }
            String res;
            try {
                AccessibilityNodeInfo root = ua.getRootInActiveWindow();
                if (root == null) throw new IllegalStateException("null root node");
                Display disp = (Display) real.invoke(g, Display.DEFAULT_DISPLAY);
                Point size = new Point();
                disp.getSize(size);
                File tmp = new File(D + "ae.xml.tmp");
                dump.invoke(null, root, tmp, disp.getRotation(), size.x, size.y);
                if (!tmp.renameTo(new File(D + "ae.xml"))) throw new IOException("rename ae.xml");
                res = "ok";
            } catch (Throwable t) {
                res = "err " + t;
            }
            try (FileWriter o = new FileWriter(D + "ae.res")) {
                o.write(res + "\n");
            }
        }
    }
}
