package ac.grim.grimac.replay;

import ac.grim.grimac.GrimAPI;
import ac.grim.grimac.api.event.GrimEventHandler;
import ac.grim.grimac.api.event.events.CompletePredictionEvent;
import ac.grim.grimac.api.plugin.GrimPlugin;
import ac.grim.grimac.checks.Check;
import ac.grim.grimac.manager.config.ConfigManagerFileImpl;
import ac.grim.grimac.manager.datastore.DataStoreLifecycle;
import ac.grim.grimac.manager.init.start.PacketManager;
import ac.grim.grimac.platform.api.PlatformLoader;
import ac.grim.grimac.player.GrimPlayer;
import com.github.retrooper.packetevents.PacketEvents;
import com.github.retrooper.packetevents.PacketEventsAPI;
import com.github.retrooper.packetevents.event.EventManager;
import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.event.PacketSendEvent;
import com.github.retrooper.packetevents.manager.InternalPacketListener;
import com.github.retrooper.packetevents.manager.server.ServerManager;
import com.github.retrooper.packetevents.manager.server.ServerVersion;
import com.github.retrooper.packetevents.protocol.ConnectionState;
import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.player.ClientVersion;
import com.github.retrooper.packetevents.protocol.player.User;
import com.github.retrooper.packetevents.protocol.player.UserProfile;
import com.github.retrooper.packetevents.settings.PacketEventsSettings;
import com.github.retrooper.packetevents.util.EventCreationUtil;
import com.github.retrooper.packetevents.util.LogManager;
import com.github.retrooper.packetevents.wrapper.handshaking.client.WrapperHandshakingClientHandshake;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerPing;
import io.github.retrooper.packetevents.impl.netty.NettyManagerImpl;
import io.netty.buffer.Unpooled;
import io.netty.channel.embedded.EmbeddedChannel;
import net.kyori.adventure.text.format.NamedTextColor;
import org.jetbrains.annotations.Nullable;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.logging.Level;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.GZIPInputStream;

import static org.mockito.Mockito.RETURNS_DEEP_STUBS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.withSettings;

// #2312 equivalence: replays recorded sessions (record_relay.py) through every listener Grim registers, at full speed,
// and writes every prediction's offset (CompletePredictionEvent, exact bits) and every flag to a TSV. Run it on two
// builds with the same recordings and diff the files: a refactor that changes nothing has to give the same file.
// Self-contained and only uses Grim's event API for the offsets, so the same file compiles on 2.0 and on #2312.
// The checks that read the wall clock are exempted on both sides, a full speed replay would trip them.
//   REPLAY_CORPUS="common/src/test/resources/replay/rec-corpus-85d931f-c1.jsonl.gz;common/src/test/resources/replay/rec-corpus-85d931f-c2.jsonl.gz" \n//   REPLAY_OUT=ticks.tsv ./gradlew :common:cleanTest :common:test --tests "*EquivalenceReplayTest"   (then diff the two builds' files)
class EquivalenceReplayTest {
    static final Pattern LINE = Pattern.compile("\"t\": ([0-9.]+), \"d\": \"(c2s|s2c)\", \"p\": \"([^\"]*)\"");
    static final Pattern MARK = Pattern.compile("FUZZ c(\\d+) s(\\d+) (start|end)");
    static final Set<String> CLOCK_CHECKS = Set.of("Timer", "TimerLimit", "NegativeTimer", "BadPacketsR", "FastBreak");

    static final EventManager EVENTS = new EventManager();
    static final List<String> WARNINGS = new java.util.concurrent.CopyOnWriteArrayList<>();
    static final Map<Object, String> SESSION_OF_USER = new HashMap<>();
    static final Map<String, int[]> PREDICTIONS = new HashMap<>();
    static PrintWriter out;
    static String segment = "";

    public static class OffsetListener {
        @GrimEventHandler
        public void onComplete(CompletePredictionEvent event) {
            String session = SESSION_OF_USER.get(event.getUser());
            if (session == null) return;
            int n = PREDICTIONS.computeIfAbsent(session, k -> new int[1])[0]++;
            double offset = event.getOffset();
            out.println(session + "\t" + n + "\t" + segment + "\t" + Long.toHexString(Double.doubleToRawLongBits(offset)) + "\t" + offset);
        }
    }

    @Test
    void replaysTheCorpus() throws Exception {
        String corpus = System.getenv("REPLAY_CORPUS");
        Assumptions.assumeTrue(corpus != null && !corpus.isBlank(), "set REPLAY_CORPUS to run");
        setUp();
        Path outPath = Path.of(System.getenv().getOrDefault("REPLAY_OUT", "build/replay-ticks.tsv"));
        int sessions = 0;
        try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(outPath))) {
            out = w;
            for (String path : corpus.split(";")) {
                String name = Path.of(path).getFileName().toString().replaceAll("\\.jsonl(\\.gz)?$", "");
                System.out.println(replay(name, path));
                sessions++;
            }
        }
        System.out.println("sessions " + sessions + ", predictions " + PREDICTIONS.values().stream().mapToInt(a -> a[0]).sum()
                + ", listener warnings " + WARNINGS.size() + " -> " + outPath.toAbsolutePath());
        WARNINGS.stream().limit(5).forEach(x -> System.out.println("  " + x));
    }

    static String replay(String name, String path) throws Exception {
        Object nativePlayer = new Object();
        User user = new User(new EmbeddedChannel(), ConnectionState.HANDSHAKING, ClientVersion.UNKNOWN, new UserProfile(null, null));
        Map<String, Double> seen = new HashMap<>();
        int packets = 0, flags = 0, caughtUp = 0;
        segment = "join";
        for (String line : lines(path)) {
            Matcher m = LINE.matcher(line);
            if (!m.find()) continue;
            byte[] bytes = Base64.getDecoder().decode(m.group(3));
            boolean c2s = m.group(2).equals("c2s");
            packets++;
            if (!c2s) {
                Matcher mark = MARK.matcher(new String(bytes, StandardCharsets.ISO_8859_1));
                if (mark.find()) segment = "s" + mark.group(2) + (mark.group(3).equals("end") ? "/after" : "");
            }
            GrimPlayer player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (player != null) {
                SESSION_OF_USER.putIfAbsent(player, name);
                exemptClockChecks(player);
            }
            if (c2s) {
                PacketReceiveEvent pre = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                EVENTS.callEvent(pre, null, true);
                PacketReceiveEvent post = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                EVENTS.callEvent(post, null, false);
                if (post.hasPostTasks()) post.getPostTasks().forEach(Runnable::run);
                if (post.getPacketType() == PacketType.Handshaking.Client.HANDSHAKE) {
                    user.setConnectionState(new WrapperHandshakingClientHandshake(EventCreationUtil.createReceiveEvent(
                            user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true)).getNextConnectionState());
                } else if (post.getPacketType() == PacketType.Login.Client.LOGIN_SUCCESS_ACK || post.getPacketType() == PacketType.Play.Client.CONFIGURATION_ACK) {
                    user.setConnectionState(ConnectionState.CONFIGURATION);
                } else if (post.getPacketType() == PacketType.Configuration.Client.CONFIGURATION_END_ACK) {
                    user.setConnectionState(ConnectionState.PLAY);
                }
            } else {
                PacketSendEvent post = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                if (player != null && post.getPacketType() == PacketType.Play.Server.PING) {
                    short id = (short) new WrapperPlayServerPing(post).getId();
                    for (int guard = 0; guard < 64 && id <= 0 && !player.didWeSendThatTrans.contains(id); guard++) {
                        player.sendTransaction();
                        caughtUp++;
                    }
                    post = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                }
                EVENTS.callEvent(post, null, false);
                PacketSendEvent pre = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                EVENTS.callEvent(pre, null, true);
                for (PacketSendEvent sent : List.of(post, pre)) {
                    if (sent.hasPostTasks()) sent.getPostTasks().forEach(Runnable::run);
                    if (sent.hasTasksAfterSend()) sent.getTasksAfterSend().forEach(Runnable::run);
                }
            }
            player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (player != null) {
                for (Object c : player.checkManager.checks) {
                    if (!(c instanceof Check check)) continue;
                    double v = check.violations;
                    if (v > seen.getOrDefault(check.getCheckName(), 0d)) {
                        out.println(name + "\tflag\t" + segment + "\t" + check.getCheckName() + "\t" + v);
                        flags++;
                    }
                    seen.put(check.getCheckName(), v);
                }
            }
        }
        GrimAPI.INSTANCE.getPlayerDataManager().remove(user);
        int[] n = PREDICTIONS.get(name);
        return name + ": " + packets + " packets, " + (n == null ? 0 : n[0]) + " predictions, " + flags + " flags, "
                + caughtUp + " pings caught up, final state " + user.getDecoderState();
    }

    static void exemptClockChecks(GrimPlayer player) throws Exception {
        for (Object c : player.checkManager.checks) {
            if (c instanceof Check check && CLOCK_CHECKS.contains(check.getCheckName())) EXEMPT.set(check, true);
        }
    }

    static Field EXEMPT;

    static List<String> lines(String path) throws Exception {
        InputStream in = Files.newInputStream(Path.of(path));
        if (path.endsWith(".gz")) in = new GZIPInputStream(in);
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            return reader.lines().toList();
        }
    }

    static void setUp() throws Exception {
        // stubOnly: a mock normally keeps every call it gets for verify(), and a long replay calls these millions of times
        PacketEventsAPI<?> api = mock(PacketEventsAPI.class, withSettings().stubOnly().defaultAnswer(RETURNS_DEEP_STUBS));
        ServerManager server = mock(ServerManager.class, withSettings().stubOnly());
        when(server.getVersion()).thenReturn(ServerVersion.V_26_3);
        when(api.getServerManager()).thenReturn(server);
        when(api.getSettings()).thenReturn(new PacketEventsSettings());
        when(api.getNettyManager()).thenReturn(new NettyManagerImpl());
        when(api.getEventManager()).thenReturn(EVENTS);
        when(api.getProtocolManager()).thenReturn(mock(com.github.retrooper.packetevents.manager.protocol.ProtocolManager.class,
                withSettings().stubOnly().defaultAnswer(RETURNS_DEEP_STUBS)));      // grim's own sends land here
        when(api.getLogManager()).thenReturn(new LogManager(api) {
            @Override
            protected void log(Level level, @Nullable NamedTextColor color, String message) {
                if (level.intValue() >= Level.WARNING.intValue()) WARNINGS.add(message);
            }

            @Override
            public void log(Level level, net.kyori.adventure.text.ComponentLike message, Throwable throwable) {
                if (level.intValue() >= Level.WARNING.intValue()) WARNINGS.add(message + (throwable == null ? "" : " " + throwable));
            }

            @Override
            public void warn(String message, Throwable throwable) {
                java.io.StringWriter trace = new java.io.StringWriter();
                throwable.printStackTrace(new PrintWriter(trace));
                WARNINGS.add(message + "\n" + trace.toString().lines().limit(12).collect(java.util.stream.Collectors.joining("\n")));
            }
        });
        PacketEvents.setAPI(api);
        PlatformLoader loader = mock(PlatformLoader.class, withSettings().stubOnly().defaultAnswer(RETURNS_DEEP_STUBS));
        GrimPlugin plugin = mock(GrimPlugin.class, withSettings().stubOnly().defaultAnswer(RETURNS_DEEP_STUBS));
        when(plugin.getDataFolder()).thenReturn(Files.createTempDirectory("grim-replay").toFile());
        when(loader.getPlugin()).thenReturn(plugin);
        when(loader.getMessagePlaceHolderManager().replacePlaceholders(org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.anyString()))
                .thenAnswer(inv -> inv.getArgument(1));
        Field field = GrimAPI.class.getDeclaredField("loader");
        field.setAccessible(true);
        field.set(GrimAPI.INSTANCE, loader);
        Field lifecycle = GrimAPI.class.getDeclaredField("dataStoreLifecycle");
        lifecycle.setAccessible(true);
        lifecycle.set(GrimAPI.INSTANCE, new DataStoreLifecycle(plugin, GrimAPI.INSTANCE.getBackendRegistry()));
        ConfigManagerFileImpl config = new ConfigManagerFileImpl();
        config.reload();
        GrimAPI.INSTANCE.getConfigManager().load(config);
        Class.forName("ac.grim.grimac.checks.impl.verbose.VerboseCodecs", true, EquivalenceReplayTest.class.getClassLoader());
        EXEMPT = Check.class.getDeclaredField("exemptPermission");
        EXEMPT.setAccessible(true);
        EVENTS.registerListener(new InternalPacketListener());
        new PacketManager().start();
        GrimAPI.INSTANCE.getEventBus().registerAnnotatedListeners(plugin, new OffsetListener());
    }
}
