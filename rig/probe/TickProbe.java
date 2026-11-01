// Attaches to a client's JDWP port, timestamps every Minecraft.tick() for N seconds, then reports the gaps.
// Two ticks in one frame (the client catching up after a slow frame) show up as a gap far under 50 ms.
import com.sun.jdi.*;
import com.sun.jdi.connect.*;
import com.sun.jdi.event.*;
import com.sun.jdi.request.*;
import java.util.*;

public class TickProbe {
    public static void main(String[] a) throws Exception {
        AttachingConnector c = Bootstrap.virtualMachineManager().attachingConnectors().stream()
                .filter(x -> x.name().equals("com.sun.jdi.SocketAttach")).findFirst().orElseThrow();
        Map<String, Connector.Argument> args = c.defaultArguments();
        args.get("hostname").setValue("127.0.0.1");
        args.get("port").setValue(a[0]);
        VirtualMachine vm = c.attach(args);
        ClassType mc = (ClassType) vm.classesByName("net.minecraft.client.Minecraft").get(0);
        BreakpointRequest bp = vm.eventRequestManager().createBreakpointRequest(mc.methodsByName("tick", "()V").get(0).location());
        bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
        bp.enable();
        Field fpsF = mc.fieldByName("fps");
        long end = System.nanoTime() + Long.parseLong(a[1]) * 1_000_000_000L, last = -1, nextFps = 0;
        List<Long> gaps = new ArrayList<>();
        List<Integer> fps = new ArrayList<>();
        try {
            while (System.nanoTime() < end) {
                EventSet set = vm.eventQueue().remove(1000);
                if (set == null) continue;
                for (Event e : set) {
                    if (!(e instanceof BreakpointEvent)) continue;
                    long now = System.nanoTime();
                    if (last >= 0) gaps.add(now - last);
                    last = now;
                    if (now > nextFps) {
                        fps.add(((IntegerValue) mc.getValue(fpsF)).value());
                        nextFps = now + 1_000_000_000L;
                    }
                }
                set.resume();
            }
        } finally {
            vm.eventRequestManager().deleteEventRequest(bp);
            vm.dispose();
        }
        long[] g = gaps.stream().mapToLong(Long::longValue).sorted().toArray();
        java.util.function.IntFunction<String> ms = i -> String.format(Locale.ROOT, "%.1f", g[Math.min(g.length - 1, Math.max(0, i))] / 1e6);
        long bunched = Arrays.stream(g).filter(x -> x < 25_000_000L).count();
        long late = Arrays.stream(g).filter(x -> x > 75_000_000L).count();
        IntSummaryStatistics f = fps.stream().mapToInt(Integer::intValue).summaryStatistics();
        System.out.printf(Locale.ROOT, "pacing port %s: %d tick gaps, min %s p1 %s p50 %s p99 %s max %s ms; under 25 ms (two ticks in one frame): %d; over 75 ms: %d; fps min %d avg %.0f max %d%n",
                a[0], g.length, ms.apply(0), ms.apply(g.length / 100), ms.apply(g.length / 2), ms.apply(g.length * 99 / 100), ms.apply(g.length - 1),
                bunched, late, f.getMin(), f.getAverage(), f.getMax());
    }
}
