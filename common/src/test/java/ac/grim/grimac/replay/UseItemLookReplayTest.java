package ac.grim.grimac.replay;

import ac.grim.grimac.GrimAPI;
import ac.grim.grimac.manager.init.start.PacketManager;
import ac.grim.grimac.player.GrimPlayer;
import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.event.PacketSendEvent;
import com.github.retrooper.packetevents.manager.InternalPacketListener;
import com.github.retrooper.packetevents.protocol.ConnectionState;
import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.player.ClientVersion;
import com.github.retrooper.packetevents.protocol.player.User;
import com.github.retrooper.packetevents.protocol.player.UserProfile;
import com.github.retrooper.packetevents.protocol.world.states.type.StateTypes;
import com.github.retrooper.packetevents.util.EventCreationUtil;
import com.github.retrooper.packetevents.util.Vector3i;
import com.github.retrooper.packetevents.wrapper.handshaking.client.WrapperHandshakingClientHandshake;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientUseItem;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerPing;
import io.netty.buffer.Unpooled;
import io.netty.channel.embedded.EmbeddedChannel;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// 11-13, the lily pad thread from #on-topic (11-09). A session recorded at the wire from a vanilla 26.3 client
// (fuzz/record_relay.py while fuzz/lilypad_test.py runs) fed through Grim's listeners like RecordedSessionReplayTest,
// with one difference: -Dreplay.useItemGapMs=N plays everything after each use item N ms later than it arrived live.
// No packet is changed, only when the next ones land, which is what a slow client tick or a network hiccup does.
// After Grim has replayed each use item (CheckManagerListener.handleQueuedPlaces, at the next movement packet) it prints
// where Grim's own world now has a lily pad. The server's answer for the same packets is in the live run's json.
//   REPLAY_RECORDING=<file> REPLAY_USEITEM_GAP_MS=30 REPLAY_OUT=<file> gradlew :common:cleanTest :common:test --tests '*UseItemLookReplayTest'
class UseItemLookReplayTest {
    // from the environment: Gradle does not hand -D on its command line to the test JVM, and the replay tests next to
    // this one read REPLAY_CORPUS / REPLAY_OUT the same way
    static final String RECORDING = System.getenv().getOrDefault("REPLAY_RECORDING", "");
    static final long GAP_MS = Long.parseLong(System.getenv().getOrDefault("REPLAY_USEITEM_GAP_MS", "0"));
    static final String OUT = System.getenv().getOrDefault("REPLAY_OUT", "");
    static final List<String> REPORT = new ArrayList<>();
    static final Pattern LINE = Pattern.compile("\"t\": ([0-9.]+), \"d\": \"(c2s|s2c)\", \"p\": \"([^\"]*)\"");
    static final Pattern MARK = Pattern.compile("FUZZ c1 s(\\d+) (start|end)");

    @Test
    void lilyPadsAfterEachUseItem() throws Exception {
        if (RECORDING.isEmpty()) return;                               // only runs when it is given a recording
        HeadlessGrimPlayerTest.setUp();
        HeadlessGrimPlayerTest.EVENTS.registerListener(new InternalPacketListener());
        new PacketManager().start();
        // cold classes can take longer than the 15 ms this is about, so the first pass only warms the JVM up
        replay(false);
        replay(true);
        REPORT.forEach(System.out::println);
        if (!OUT.isEmpty()) java.nio.file.Files.write(java.nio.file.Path.of(OUT), REPORT, StandardCharsets.UTF_8);
    }

    void replay(boolean print) throws Exception {
        Object nativePlayer = new Object();
        User user = new User(new EmbeddedChannel(), ConnectionState.HANDSHAKING, ClientVersion.UNKNOWN, new UserProfile(null, null));
        String segment = "join";
        long shift = 0;
        int uses = 0;
        boolean pending = false;
        String pendingLine = "";
        long usedAt = 0;
        List<Vector3i> watch = new ArrayList<>();                      // pads Grim placed, until they are gone again
        long watchSince = 0;
        String lastType = "";
        long start = System.nanoTime();
        for (String line : RecordedSessionReplayTest.lines(RECORDING)) {
            Matcher m = LINE.matcher(line);
            if (!m.find()) continue;
            byte[] bytes = Base64.getDecoder().decode(m.group(3));
            long due = start + (long) (Double.parseDouble(m.group(1)) * 1_000_000) + shift;
            long wait = due - System.nanoTime();
            if (wait > 0) Thread.sleep(wait / 1_000_000, (int) (wait % 1_000_000));
            boolean c2s = m.group(2).equals("c2s");
            if (!c2s) {
                Matcher mark = MARK.matcher(new String(bytes, StandardCharsets.ISO_8859_1));
                if (mark.find()) segment = "s" + mark.group(1) + " " + mark.group(2);
            }
            GrimPlayer player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (c2s) {
                PacketReceiveEvent pre = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                HeadlessGrimPlayerTest.EVENTS.callEvent(pre, null, true);
                PacketReceiveEvent post = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                boolean useItem = player != null && post.getPacketType() == PacketType.Play.Client.USE_ITEM;
                if (useItem) {
                    WrapperPlayClientUseItem use = new WrapperPlayClientUseItem(
                            EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true));
                    pendingLine = String.format("use item %d in %s: the packet says yaw %.1f pitch %.1f, Grim's look when it arrived yaw %.1f pitch %.1f",
                            ++uses, segment, use.getYaw(), use.getPitch(), player.yaw, player.pitch);
                }
                HeadlessGrimPlayerTest.EVENTS.callEvent(post, null, false);
                if (post.hasPostTasks()) post.getPostTasks().forEach(Runnable::run);
                lastType = "c2s " + post.getPacketType();
                if (useItem) {
                    pending = true;
                    usedAt = System.nanoTime();
                    shift += GAP_MS * 1_000_000;                       // everything after this use item lands GAP_MS later
                } else if (pending && player != null && player.placeUseItemPackets.isEmpty()) {
                    // Grim has just replayed the use item: this is its own world right after, before any answer from the server
                    pending = false;
                    List<String> pads = new ArrayList<>();
                    int px = (int) Math.floor(player.x), py = (int) Math.floor(player.y), pz = (int) Math.floor(player.z);
                    for (int x = px - 6; x <= px + 6; x++)
                        for (int y = py - 2; y <= py + 1; y++)
                            for (int z = pz - 6; z <= pz + 6; z++)
                                if (player.compensatedWorld.getBlock(new Vector3i(x, y, z)).getType() == StateTypes.LILY_PAD) {
                                    pads.add((x - px) + "," + (y - py) + "," + (z - pz));
                                    watch.add(new Vector3i(x, y, z));
                                }
                    watchSince = System.nanoTime();
                    if (print) REPORT.add(pendingLine + String.format(", replayed %.1f ms later on a %s -> Grim's lily pads (relative to the player): %s",
                            (System.nanoTime() - usedAt) / 1e6, post.getPacketType(), pads.isEmpty() ? "none" : pads));
                }
                if (post.getPacketType() == PacketType.Handshaking.Client.HANDSHAKE) {
                    user.setConnectionState(new WrapperHandshakingClientHandshake(
                            EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true)).getNextConnectionState());
                } else if (post.getPacketType() == PacketType.Login.Client.LOGIN_SUCCESS_ACK
                        || post.getPacketType() == PacketType.Play.Client.CONFIGURATION_ACK) {
                    user.setConnectionState(ConnectionState.CONFIGURATION);
                } else if (post.getPacketType() == PacketType.Configuration.Client.CONFIGURATION_END_ACK) {
                    user.setConnectionState(ConnectionState.PLAY);
                }
            } else {
                PacketSendEvent post = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                if (player != null && post.getPacketType() == PacketType.Play.Server.PING) {
                    short id = (short) new WrapperPlayServerPing(post).getId();
                    for (int guard = 0; guard < 64 && id <= 0 && !player.didWeSendThatTrans.contains(id); guard++) player.sendTransaction();
                    post = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                }
                lastType = "s2c " + post.getPacketType();
                HeadlessGrimPlayerTest.EVENTS.callEvent(post, null, false);
                PacketSendEvent pre = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                HeadlessGrimPlayerTest.EVENTS.callEvent(pre, null, true);
                for (PacketSendEvent sent : List.of(post, pre)) {
                    if (sent.hasPostTasks()) sent.getPostTasks().forEach(Runnable::run);
                    if (sent.hasTasksAfterSend()) sent.getTasksAfterSend().forEach(Runnable::run);
                }
            }
            // a pad Grim placed: how long is it in Grim's world, and what takes it away
            GrimPlayer now = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (!watch.isEmpty() && !pending && now != null) {
                final GrimPlayer gp = now;
                if (watch.stream().noneMatch(v -> gp.compensatedWorld.getBlock(v).getType() == StateTypes.LILY_PAD)) {
                    if (print) REPORT.add(String.format("    gone from Grim's world %.1f ms after it was placed, on %s", (System.nanoTime() - watchSince) / 1e6, lastType));
                    watch.clear();
                }
            }
        }
        GrimAPI.INSTANCE.getPlayerDataManager().remove(user);
        if (print) REPORT.add("replayed with a " + GAP_MS + " ms gap after each of " + uses + " use items");
    }
}
