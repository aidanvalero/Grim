// Presses jump/crouch in the vanilla client by setting the same KeyMapping.isDown flag a real key press
// sets, once per client tick over JDWP. Works with the PC locked (no window focus needed) and puts every
// press on an exact tick. Nothing in the game's movement code is touched; Grim only sees normal packets.
//
//   java KeyDriver schedule.txt [names.properties]
//     schedule lines: "<tick> down|up jump|sneak|forward|back|left|right|sprint|use|attack", "<tick> rcon <command>", "<tick> end",
//                     "<tick> attack crosshair|<player name>", "<tick> look <yaw> <pitch>", "<tick> respawn",
//                     "<tick> sync <x> <y> <z>": hold the schedule until the player is exactly there (a teleport landed),
//                     so whatever follows starts on the first tick after it
//                     "<tick> remind": this tick sends a position packet even without moving (positionReminder = 19),
//                     "<tick> state <vx> <vy> <vz> <onGround 0/1>": velocity and onGround before this tick moves
//                     "<tick> fastmath on|off": swap Mth.SIN for OptiFine's 4096-step fast math table (26.x names) and back
//                     "<tick> click <containerId> <slot> <button> <PICKUP|QUICK_MOVE|...>", "<tick> close": inventory click / close (26.x names)
//                     "<tick> grab on|off": treat the mouse as grabbed, so a held attack key digs like a real held click (26.x names)
//     EXACT_TRACE=<path>: every tick, the state the previous tick left, full precision (see exactLine)
//     names.properties: obfuscated names for pre-26.1 clients (see mappings.py); Mojang names otherwise
import com.sun.jdi.*;
import com.sun.jdi.connect.AttachingConnector;
import com.sun.jdi.connect.Connector;
import com.sun.jdi.event.*;
import com.sun.jdi.request.BreakpointRequest;
import com.sun.jdi.request.EventRequest;

import java.io.DataInputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.LinkedBlockingQueue;

public class KeyDriver {
    record Step(int tick, String action, String arg) {}

    // OptiFine fast math, built exactly like Grim's OptifineFastMath: 4096 entries rounded to 8 decimals
    static final float[] OPT = new float[4096];
    static { for (int k = 0; k < 4096; k++) OPT[k] = (float) ((double) Math.round(StrictMath.sin(k * Math.PI * 2d / 4096d) * 1.0E8d) / 1.0E8d); }
    static List<Value> vanillaSin;

    public static void main(String[] args) throws Exception {
        List<Step> steps = new ArrayList<>();
        for (String line : Files.readAllLines(Path.of(args[0]))) {
            line = line.strip();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] p = line.split(" ", 3);
            steps.add(new Step(Integer.parseInt(p[0]), p[1], p.length > 2 ? p[2] : ""));
        }

        Properties n = new Properties();
        String[] defaults = {"minecraft", "net.minecraft.client.Minecraft", "instance", "instance", "options", "options",
                "tick", "tick", "keyUse", "keyUse", "keyAttack", "keyAttack", "keyJump", "keyJump", "keyShift", "keyShift", "keyUp", "keyUp", "keySprint", "keySprint",
                "keyMapping", "net.minecraft.client.KeyMapping", "isDown", "isDown",
                "player", "player", "entity", "net.minecraft.world.entity.Entity", "deltaMovement", "deltaMovement",
                "vec3", "net.minecraft.world.phys.Vec3", "keyDown", "keyDown", "keyLeft", "keyLeft", "keyRight", "keyRight",
                "yaw", "yRot", "prevYaw", "yRotO", "pitch", "xRot", "prevPitch", "xRotO", "position", "position",
                "vx", "x", "vy", "y", "vz", "z", "onGround", "onGround", "bb", "bb", "aabbMinY", "minY", "aabbMaxY", "maxY",
                "localPlayer", "net.minecraft.client.player.LocalPlayer", "crouching", "crouching", "positionReminder", "positionReminder",
                "respawn", "respawn", "showScreen", "setScreen", "showScreenSig", "(Lnet/minecraft/client/gui/screens/Screen;)V",
                "vehicle", "vehicle", "horseJumpPending", "playerJumpPendingScale"};
        for (int d = 0; d < defaults.length; d += 2) n.setProperty(defaults[d], defaults[d + 1]);
        if (args.length > 1) try (var r = Files.newBufferedReader(Path.of(args[1]))) { n.load(r); }

        AttachingConnector conn = Bootstrap.virtualMachineManager().attachingConnectors().stream()
                .filter(c -> c.name().equals("com.sun.jdi.SocketAttach")).findFirst().orElseThrow();
        Map<String, Connector.Argument> a = conn.defaultArguments();
        a.get("hostname").setValue("127.0.0.1");
        a.get("port").setValue(System.getenv().getOrDefault("JDWP_PORT", "5005"));   // one driver per client when fuzzing
        VirtualMachine vm = conn.attach(a);

        ClassType mcClass = (ClassType) vm.classesByName(n.getProperty("minecraft")).get(0);
        ObjectReference mc = (ObjectReference) mcClass.getValue(mcClass.fieldByName(n.getProperty("instance")));
        ObjectReference options = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("options")));
        ReferenceType optionsType = options.referenceType();
        // Only the keys this names file knows about; scheduling one it doesn't is an error, not a silent no-op
        String[][] keyNames = {{"jump", "keyJump"}, {"sneak", "keyShift"}, {"forward", "keyUp"}, {"sprint", "keySprint"},
                {"back", "keyDown"}, {"left", "keyLeft"}, {"right", "keyRight"}, {"use", "keyUse"}, {"attack", "keyAttack"}};   // use / attack: right / left click, 26.x and 1.12.2 names
        Map<String, ObjectReference> keys = new HashMap<>();
        for (String[] k : keyNames) {
            Field f = optionsType.fieldByName(n.getProperty(k[1]));
            if (f != null) keys.put(k[0], (ObjectReference) options.getValue(f));
        }
        Field isDown = ((ClassType) vm.classesByName(n.getProperty("keyMapping")).get(0)).fieldByName(n.getProperty("isDown"));
        Map<String, Boolean> want = new HashMap<>();
        for (String k : keys.keySet()) want.put(k, false);

        Method tick = mcClass.methodsByName(n.getProperty("tick"), "()V").get(0);
        BreakpointRequest bp = vm.eventRequestManager().createBreakpointRequest(tick.location());
        bp.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
        bp.enable();

        // RCON runs on its own thread so the client thread never waits on the server
        LinkedBlockingQueue<String> rconQueue = new LinkedBlockingQueue<>();
        Thread rconThread = new Thread(() -> {
            try (Rcon rcon = new Rcon()) {
                while (true) {
                    String cmd = rconQueue.take();
                    if (cmd.equals("\0")) return;
                    rcon.send(cmd);
                }
            } catch (Exception e) { System.out.println("rcon error: " + e); }
        });
        rconThread.setDaemon(true);
        rconThread.start();

        int t = 0, i = 0, hits = 0, misses = 0, clientTick = 0, syncWait = 0;
        boolean done = false;
        // A held attack key only digs while the mouse is grabbed (Minecraft.handleKeybinds -> continueAttack), and a
        // hidden rig window never grabs it. "grab on" sets MouseHandler.mouseGrabbed every tick (26.x names), or the
        // boolean the names file calls grabField on Minecraft itself (1.12.2: inGameHasFocus, which processKeyBinds
        // checks before sendClickBlockToController)
        ObjectReference mouse;
        Field mouseGrabbed;
        if (n.getProperty("grabField") != null) {
            mouse = mc;
            mouseGrabbed = mcClass.fieldByName(n.getProperty("grabField"));
        } else {
            // obfuscated Mojang-mapped clients (1.14.4, 10-31) give both names in the names file
            Field mouseField = mcClass.fieldByName(n.getProperty("mouseHandler", "mouseHandler"));
            mouse = mouseField == null ? null : (ObjectReference) mc.getValue(mouseField);
            mouseGrabbed = mouse == null ? null : mouse.referenceType().fieldByName(n.getProperty("mouseGrabbed", "mouseGrabbed"));
        }
        boolean grab = false;
        String exactFile = System.getenv("EXACT_TRACE");
        java.io.PrintWriter exact = exactFile == null ? null : new java.io.PrintWriter(Files.newBufferedWriter(Path.of(exactFile)), true);
        // TRACE_FILE=<path> (26.x clients, Mojang names): one line per client tick with the player's real state at the
        // start of that tick, so Grim's per-tick guesses can be lined up against what vanilla actually did
        String traceFile = System.getenv("TRACE_FILE");
        java.io.PrintWriter trace = traceFile == null ? null : new java.io.PrintWriter(Files.newBufferedWriter(Path.of(traceFile)), true);
        System.out.println("attached; running " + steps.size() + " steps");
        // Any failure below must still release the client: a breakpoint left behind freezes the game
        try {
        while (!done) {
            EventSet set = vm.eventQueue().remove();
            for (Event e : set) {
                if (!(e instanceof BreakpointEvent)) continue;
                boolean holding = false;
                steps:
                while (i < steps.size() && steps.get(i).tick() <= t) {
                    Step s = steps.get(i);
                    if (s.action().equals("sync")) {
                        String[] xyz = s.arg().split(" ");
                        double[] at = readPos(vm, mc, mcClass, n);
                        // Exactly zero velocity too: only a teleport that just landed has that. A player who walked or
                        // fell onto the same spot has gravity in it (-0.0784), and syncing on that starts a tick early
                        double[] vel = at == null ? null : readVel(vm, mc, mcClass, n);
                        if (at == null || at[0] != Double.parseDouble(xyz[0]) || at[1] != Double.parseDouble(xyz[1]) || at[2] != Double.parseDouble(xyz[2])
                                || vel[0] != 0 || vel[1] != 0 || vel[2] != 0) {
                            if (++syncWait > 600) throw new IllegalStateException("sync never reached " + s.arg() + ", player at " + Arrays.toString(at));
                            holding = true;
                            break steps;
                        }
                        System.out.println("synced on client tick " + clientTick + " after " + syncWait + " ticks");
                        syncWait = 0;
                        i++;
                        continue;
                    }
                    i++;
                    switch (s.action()) {
                        case "down", "up" -> {
                            if (!keys.containsKey(s.arg())) throw new IllegalArgumentException("no key mapping for " + s.arg() + " in this names file");
                            want.put(s.arg(), s.action().equals("down"));
                        }
                        case "rcon" -> rconQueue.add(s.arg());
                        // Positive control: an upward shove the client applies and Grim cannot predict,
                        // to prove a real violation shows up in the server log
                        case "kick" -> {
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ClassType entity = (ClassType) vm.classesByName(n.getProperty("entity")).get(0);
                            if (n.getProperty("motionY") != null) {
                                // 1.8 keeps velocity as three doubles on Entity instead of a Vec3
                                player.setValue(entity.fieldByName(n.getProperty("motionY")), vm.mirrorOf(Double.parseDouble(s.arg())));
                            } else {
                                ClassType vec3 = (ClassType) vm.classesByName(n.getProperty("vec3")).get(0);
                                Method ctor = vec3.concreteMethodByName("<init>", "(DDD)V");
                                ObjectReference v = vec3.newInstance(th, ctor, List.of(vm.mirrorOf(0.0), vm.mirrorOf(Double.parseDouble(s.arg())), vm.mirrorOf(0.0)), ClassType.INVOKE_SINGLE_THREADED);
                                player.setValue(entity.fieldByName(n.getProperty("deltaMovement")), v);
                            }
                        }
                        // What Minecraft.clickMouse does on an entity hit: swing, then PlayerControllerMP.attackEntity.
                        // "crosshair" hits whatever the crosshair is on (a miss only swings); a player name hits that player
                        case "attack" -> {
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ObjectReference target = null;
                            if (s.arg().equals("crosshair")) {
                                ObjectReference hit = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("hitResult")));
                                if (hit != null) target = (ObjectReference) hit.getValue(hit.referenceType().fieldByName(n.getProperty("hitEntity")));
                            } else {
                                ObjectReference level = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("level")));
                                Method byName = level.referenceType().methodsByName(n.getProperty("playerByName"), n.getProperty("playerByNameSig")).get(0);
                                target = (ObjectReference) level.invokeMethod(th, byName, List.of(vm.mirrorOf(s.arg())), ObjectReference.INVOKE_SINGLE_THREADED);
                            }
                            Method swing = vm.classesByName(n.getProperty("livingEntity")).get(0).methodsByName(n.getProperty("swing"), "()V").get(0);
                            player.invokeMethod(th, swing, List.of(), ObjectReference.INVOKE_SINGLE_THREADED);
                            if (target != null) {
                                ObjectReference gameMode = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("gameMode")));
                                Method attack = gameMode.referenceType().methodsByName(n.getProperty("attack"), n.getProperty("attackSig")).get(0);
                                gameMode.invokeMethod(th, attack, List.of(player, target), ObjectReference.INVOKE_SINGLE_THREADED);
                                hits++;
                            } else misses++;
                        }
                        // One left click the way 26.x handles it (Minecraft.startAttack: attack whatever the crosshair is on,
                        // or reset the attack strength on a miss, then swing). "attack" above only has 1.8.9 names
                        case "startattack" -> {
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            Method start = mcClass.methodsByName("startAttack", "()Z").get(0);
                            boolean ok = ((BooleanValue) mc.invokeMethod(th, start, List.of(), ObjectReference.INVOKE_SINGLE_THREADED)).value();
                            System.out.println("startAttack on client tick " + clientTick + ": " + ok);
                        }
                        // Turn the camera like the mouse does (rotation fields only). Unlike a teleport this leaves the
                        // hitbox alone, so a box that collision pressed flat against a wall stays exactly there
                        case "look" -> {
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ReferenceType entity = vm.classesByName(n.getProperty("entity")).get(0);
                            String[] yp = s.arg().split(" ");
                            for (String f : new String[]{"yaw", "prevYaw"}) player.setValue(entity.fieldByName(n.getProperty(f)), vm.mirrorOf(Float.parseFloat(yp[0])));
                            for (String f : new String[]{"pitch", "prevPitch"}) player.setValue(entity.fieldByName(n.getProperty(f)), vm.mirrorOf(Float.parseFloat(yp[1])));
                        }
                        // What the death screen's Respawn button does: EntityPlayerSP.respawnPlayer, then close the screen
                        case "respawn" -> {
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            Method respawn = player.referenceType().methodsByName(n.getProperty("respawn"), "()V").get(0);
                            player.invokeMethod(th, respawn, List.of(), ObjectReference.INVOKE_SINGLE_THREADED);
                            // 26.x has no Minecraft.setScreen and closes the death screen itself when the respawn arrives
                            List<Method> show = mcClass.methodsByName(n.getProperty("showScreen"), n.getProperty("showScreenSig"));
                            if (!show.isEmpty()) mc.invokeMethod(th, show.get(0), Collections.singletonList(null), ObjectReference.INVOKE_SINGLE_THREADED);
                        }
                        // Start a replay mid-motion: the velocity and onGround the logged player had at the start of the
                        // tick (a teleport leaves zero velocity). Exact doubles, set before the tick moves
                        case "state" -> {
                            String[] v = s.arg().split(" ");
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ClassType entity = (ClassType) vm.classesByName(n.getProperty("entity")).get(0);
                            if (n.getProperty("posX") != null) {
                                player.setValue(entity.fieldByName(n.getProperty("motionX")), vm.mirrorOf(Double.parseDouble(v[0])));
                                player.setValue(entity.fieldByName(n.getProperty("motionY")), vm.mirrorOf(Double.parseDouble(v[1])));
                                player.setValue(entity.fieldByName(n.getProperty("motionZ")), vm.mirrorOf(Double.parseDouble(v[2])));
                            } else {
                                ClassType vec3 = (ClassType) vm.classesByName(n.getProperty("vec3")).get(0);
                                Method ctor = vec3.concreteMethodByName("<init>", "(DDD)V");
                                ObjectReference dm = vec3.newInstance(th, ctor, List.of(vm.mirrorOf(Double.parseDouble(v[0])), vm.mirrorOf(Double.parseDouble(v[1])), vm.mirrorOf(Double.parseDouble(v[2]))), ClassType.INVOKE_SINGLE_THREADED);
                                player.setValue(entity.fieldByName(n.getProperty("deltaMovement")), dm);
                            }
                            player.setValue(entity.fieldByName(n.getProperty("onGround")), vm.mirrorOf(v[3].equals("1")));
                        }
                        // Make this tick's sendPosition send the position even if the player didn't move: the same
                        // packet vanilla sends every 20th still tick (positionReminder reaching 20), just on this tick
                        case "remind" -> {
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ReferenceType lp = vm.classesByName(n.getProperty("localPlayer")).get(0);
                            player.setValue(lp.fieldByName(n.getProperty("positionReminder")), vm.mirrorOf(19));
                        }
                        // Emulate OptiFine fast math on this vanilla client: every Mth.SIN entry becomes the 4096-step
                        // entry it falls in (OPT[i >> 4]). Same table entry as OptiFine for 99.99% of positive angles,
                        // one step off for most negative ones. "off" puts the vanilla table back
                        case "fastmath" -> {
                            ClassType mth = (ClassType) vm.classesByName("net.minecraft.util.Mth").get(0);
                            ArrayReference sin = (ArrayReference) mth.getValue(mth.fieldByName("SIN"));
                            if (vanillaSin == null) vanillaSin = sin.getValues();
                            if (s.arg().equals("on")) {
                                List<Value> fast = new ArrayList<>(65536);
                                for (int k = 0; k < 65536; k++) fast.add(vm.mirrorOf(OPT[k >> 4]));
                                sin.setValues(fast);
                            } else sin.setValues(vanillaSin);
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            Method msin = mth.methodsByName("sin", "(D)F").get(0);
                            for (float deg : new float[]{37.5f, 199.9f, 376.3f}) {
                                float r = deg * ((float) Math.PI / 180f);
                                float got = ((FloatValue) mth.invokeMethod(th, msin, List.of(vm.mirrorOf((double) r)), ClassType.INVOKE_SINGLE_THREADED)).value();
                                float van = ((FloatValue) vanillaSin.get((int) ((long) (r * 10430.378350470453) & 65535L))).value();
                                System.out.printf(Locale.ROOT, "fastmath %s on client tick %d: Mth.sin(%s deg) = %s, optifine %s, vanilla %s%n",
                                        s.arg(), clientTick, deg, got, OPT[(int) (r * 651.8986f) & 4095], van);
                            }
                        }
                        // A mouse click in the open menu, through the client's own MultiPlayerGameMode.handleContainerInput
                        // (predicts the click, then sends it with the changed slots): "<containerId> <slot> <button> <PICKUP|QUICK_MOVE|...>".
                        // 26.x names only. Needs no screen, the click goes to player.containerMenu (0 = own inventory)
                        case "click" -> {
                            String[] c = s.arg().split(" ");
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            ObjectReference gameMode = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("gameMode", "gameMode")));
                            ClassType input = (ClassType) vm.classesByName("net.minecraft.world.inventory.ContainerInput").get(0);
                            Method handle = gameMode.referenceType().methodsByName("handleContainerInput").get(0);
                            gameMode.invokeMethod(th, handle, List.of(vm.mirrorOf(Integer.parseInt(c[0])), vm.mirrorOf(Integer.parseInt(c[1])),
                                    vm.mirrorOf(Integer.parseInt(c[2])), input.getValue(input.fieldByName(c[3])), player), ObjectReference.INVOKE_SINGLE_THREADED);
                        }
                        // What closing the inventory screen sends: LocalPlayer.closeContainer (26.x names)
                        case "close" -> {
                            ThreadReference th = ((BreakpointEvent) e).thread();
                            ObjectReference player = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
                            player.invokeMethod(th, player.referenceType().methodsByName("closeContainer", "()V").get(0), List.of(), ObjectReference.INVOKE_SINGLE_THREADED);
                        }
                        case "grab" -> {
                            grab = s.arg().equals("on");
                            if (!grab) mouse.setValue(mouseGrabbed, vm.mirrorOf(false));
                        }
                        case "end" -> done = true;
                        default -> throw new IllegalArgumentException(s.action());
                    }
                }
                // Re-applied every tick so nothing in the client can clear a held key between ticks
                for (var k : want.entrySet()) keys.get(k.getKey()).setValue(isDown, vm.mirrorOf(k.getValue()));
                if (grab) mouse.setValue(mouseGrabbed, vm.mirrorOf(true));
                if (trace != null) trace.println(t + " " + traceLine(vm, mc, mcClass) + " keys shift=" + (want.get("sneak") ? 1 : 0));
                if (exact != null) exact.println(t + " " + clientTick + (holding ? " hold " : " run ") + exactLine(vm, mc, mcClass, n) + " keys="
                        + String.join(",", want.entrySet().stream().filter(Map.Entry::getValue).map(Map.Entry::getKey).sorted().toList()));
                clientTick++;
                if (!holding) t++;
            }
            set.resume();
        }
        } finally {
            try {
                for (var k : keys.values()) k.setValue(isDown, vm.mirrorOf(false));
                vm.eventRequestManager().deleteEventRequest(bp);
            } finally {
                vm.dispose();   // resumes anything still suspended
                if (trace != null) trace.close();
                if (exact != null) exact.close();
            }
            rconQueue.add("\0");
            rconThread.join(2000);
        }
        System.out.println("done after " + t + " ticks" + (hits + misses > 0 ? ", attacks: " + hits + " hit, " + misses + " missed" : ""));
    }

    // State left by the previous client tick: position, deltaMovement (the velocity this tick starts from), onGround,
    // LocalPlayer.crouching (decides this tick's slowdown), the shift key the input last read, and zza (last forward input)
    static String traceLine(VirtualMachine vm, ObjectReference mc, ClassType mcClass) {
        try {
            ObjectReference p = (ObjectReference) mc.getValue(mcClass.fieldByName("player"));
            if (p == null) return "no-player";
            ReferenceType entity = vm.classesByName("net.minecraft.world.entity.Entity").get(0);
            ReferenceType living = vm.classesByName("net.minecraft.world.entity.LivingEntity").get(0);
            ReferenceType local = vm.classesByName("net.minecraft.client.player.LocalPlayer").get(0);
            ObjectReference pos = (ObjectReference) p.getValue(entity.fieldByName("position"));
            ObjectReference dm = (ObjectReference) p.getValue(entity.fieldByName("deltaMovement"));
            ObjectReference input = (ObjectReference) p.getValue(local.fieldByName("input"));
            ObjectReference presses = (ObjectReference) input.getValue(input.referenceType().fieldByName("keyPresses"));
            return String.format(Locale.ROOT, "pos=(%.5f,%.5f,%.5f) dm=(%.5f,%.5f,%.5f) g=%d crouching=%d shift=%d zza=%.4f",
                    vec(pos, "x"), vec(pos, "y"), vec(pos, "z"), vec(dm, "x"), vec(dm, "y"), vec(dm, "z"),
                    ((BooleanValue) p.getValue(entity.fieldByName("onGround"))).value() ? 1 : 0,
                    ((BooleanValue) p.getValue(local.fieldByName("crouching"))).value() ? 1 : 0,
                    ((BooleanValue) presses.getValue(presses.referenceType().fieldByName("shift"))).value() ? 1 : 0,
                    ((FloatValue) p.getValue(living.fieldByName("zza"))).value());
        } catch (Exception ex) {
            return "trace-error " + ex;
        }
    }

    // Where the player is now, from the names file: 1.8 keeps posX/posY/posZ doubles on Entity, later versions a Vec3
    static double[] readPos(VirtualMachine vm, ObjectReference mc, ClassType mcClass, Properties n) {
        ObjectReference p = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
        if (p == null) return null;
        ReferenceType entity = vm.classesByName(n.getProperty("entity")).get(0);
        if (n.getProperty("posX") != null) return new double[]{dbl(p, entity, n.getProperty("posX")), dbl(p, entity, n.getProperty("posY")), dbl(p, entity, n.getProperty("posZ"))};
        return vec3(entity, p, n.getProperty("position"), n);
    }

    static double[] readVel(VirtualMachine vm, ObjectReference mc, ClassType mcClass, Properties n) {
        ObjectReference p = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
        ReferenceType entity = vm.classesByName(n.getProperty("entity")).get(0);
        // 1.14.4 has the position as doubles but the velocity already as a Vec3, so this goes by motionX alone
        if (n.getProperty("motionX") != null) return new double[]{dbl(p, entity, n.getProperty("motionX")), dbl(p, entity, n.getProperty("motionY")), dbl(p, entity, n.getProperty("motionZ"))};
        return vec3(entity, p, n.getProperty("deltaMovement"), n);
    }

    static double[] vec3(ReferenceType owner, ObjectReference o, String field, Properties n) {
        ObjectReference v = (ObjectReference) o.getValue(owner.fieldByName(field));
        ReferenceType vt = v.referenceType();
        return new double[]{((DoubleValue) v.getValue(vt.fieldByName(n.getProperty("vx")))).value(),
                ((DoubleValue) v.getValue(vt.fieldByName(n.getProperty("vy")))).value(),
                ((DoubleValue) v.getValue(vt.fieldByName(n.getProperty("vz")))).value()};
    }

    static double dbl(ObjectReference o, ReferenceType owner, String field) {
        return ((DoubleValue) o.getValue(owner.fieldByName(field))).value();
    }

    // Position, velocity, onGround, hitbox bottom/top and (1.14+) LocalPlayer.crouching, as the previous tick left them,
    // every double printed exactly (Double.toString round-trips) so a replay can be checked against a log digit for digit
    static String exactLine(VirtualMachine vm, ObjectReference mc, ClassType mcClass, Properties n) {
        try {
            ObjectReference p = (ObjectReference) mc.getValue(mcClass.fieldByName(n.getProperty("player")));
            if (p == null) return "no-player";
            ReferenceType entity = vm.classesByName(n.getProperty("entity")).get(0);
            double[] pos = readPos(vm, mc, mcClass, n);
            double[] dm = n.getProperty("posX") != null
                    ? new double[]{dbl(p, entity, n.getProperty("motionX")), dbl(p, entity, n.getProperty("motionY")), dbl(p, entity, n.getProperty("motionZ"))}
                    : vec3(entity, p, n.getProperty("deltaMovement"), n);
            boolean ground = ((BooleanValue) p.getValue(entity.fieldByName(n.getProperty("onGround")))).value();
            ObjectReference bb = (ObjectReference) p.getValue(entity.fieldByName(n.getProperty("bb")));
            double minY = dbl(bb, bb.referenceType(), n.getProperty("aabbMinY")), maxY = dbl(bb, bb.referenceType(), n.getProperty("aabbMaxY"));
            String crouch = "-";
            List<ReferenceType> lp = vm.classesByName(n.getProperty("localPlayer"));
            if (!lp.isEmpty() && lp.get(0).fieldByName(n.getProperty("crouching")) != null)
                crouch = ((BooleanValue) p.getValue(lp.get(0).fieldByName(n.getProperty("crouching")))).value() ? "1" : "0";
            return "pos=" + pos[0] + "," + pos[1] + "," + pos[2] + " dm=" + dm[0] + "," + dm[1] + "," + dm[2]
                    + " g=" + (ground ? 1 : 0) + " bb=" + minY + "," + maxY + " crouching=" + crouch + sprintLine(vm, p, entity) + vehicleLine(p, entity, n);
        } catch (Exception ex) {
            return "trace-error " + ex;
        }
    }

    // SPRINT_TRACE=1 (26.x names, 10-29 dismount runs): the client's sprint flag (shared flag bit 3), the sprint state it
    // last sent the server (LocalPlayer.wasSprinting) and its movement speed attribute as of its last aiStep
    // (LivingEntity.speed: 0.1 walking, 0.13 with the sprint modifier). Off by default so the trace format doesn't change
    static String sprintLine(VirtualMachine vm, ObjectReference p, ReferenceType entity) {
        if (System.getenv("SPRINT_TRACE") == null) return "";
        try {
            ObjectReference data = (ObjectReference) p.getValue(entity.fieldByName("entityData"));
            ArrayReference items = (ArrayReference) data.getValue(data.referenceType().fieldByName("itemsById"));
            ObjectReference flags = (ObjectReference) items.getValue(0);
            ObjectReference boxed = (ObjectReference) flags.getValue(flags.referenceType().fieldByName("value"));
            byte b = ((ByteValue) boxed.getValue(boxed.referenceType().fieldByName("value"))).value();
            ReferenceType lp = vm.classesByName("net.minecraft.client.player.LocalPlayer").get(0);
            boolean sent = ((BooleanValue) p.getValue(lp.fieldByName("wasSprinting"))).value();
            ReferenceType le = vm.classesByName("net.minecraft.world.entity.LivingEntity").get(0);
            float speed = ((FloatValue) p.getValue(le.fieldByName("speed"))).value();
            return " sprint=" + ((b & 8) != 0 ? 1 : 0) + " sent=" + (sent ? 1 : 0) + " speedattr=" + speed;
        } catch (Exception ex) {
            return " sprint=err:" + ex.getClass().getSimpleName();
        }
    }

    // The vehicle the player rides, as the previous tick left it: position, velocity, onGround, and for a horse the jump
    // the rider queued (playerJumpPendingScale, used on the horse's next tick if it's on the ground)
    static String vehicleLine(ObjectReference p, ReferenceType entity, Properties n) {
        if (n.getProperty("vehicle") == null || entity.fieldByName(n.getProperty("vehicle")) == null) return "";
        ObjectReference v = (ObjectReference) p.getValue(entity.fieldByName(n.getProperty("vehicle")));
        if (v == null) return "";
        double[] pos = vec3(entity, v, n.getProperty("position"), n), dm = vec3(entity, v, n.getProperty("deltaMovement"), n);
        boolean ground = ((BooleanValue) v.getValue(entity.fieldByName(n.getProperty("onGround")))).value();
        String jump = "-";
        for (ReferenceType t = v.referenceType(); t instanceof ClassType ct; t = ct.superclass()) {
            Field f = ct.fieldByName(n.getProperty("horseJumpPending"));
            if (f != null && f.declaringType().equals(ct)) { jump = String.valueOf(((FloatValue) v.getValue(f)).value()); break; }
        }
        return " | veh pos=" + pos[0] + "," + pos[1] + "," + pos[2] + " dm=" + dm[0] + "," + dm[1] + "," + dm[2]
                + " g=" + (ground ? 1 : 0) + " jumpPending=" + jump;
    }

    static double vec(ObjectReference v, String f) {
        return ((DoubleValue) v.getValue(v.referenceType().fieldByName(f))).value();
    }

    static class Rcon implements AutoCloseable {
        private final Socket socket = new Socket("127.0.0.1", 25598);
        private final OutputStream out = socket.getOutputStream();
        private final DataInputStream in = new DataInputStream(socket.getInputStream());
        private int id = 1;

        Rcon() throws Exception { packet(3, "repro"); }

        void send(String cmd) throws Exception { packet(2, cmd); }

        private void packet(int type, String body) throws Exception {
            byte[] b = body.getBytes(StandardCharsets.UTF_8);
            ByteBuffer buf = ByteBuffer.allocate(14 + b.length).order(ByteOrder.LITTLE_ENDIAN);
            buf.putInt(10 + b.length).putInt(id++).putInt(type).put(b).put((byte) 0).put((byte) 0);
            out.write(buf.array());
            out.flush();
            int len = Integer.reverseBytes(in.readInt());
            in.readNBytes(len);
        }

        public void close() throws Exception { socket.close(); }
    }
}
