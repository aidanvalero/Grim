package ac.grim.grimac.replay;

import ac.grim.grimac.GrimAPI;
import ac.grim.grimac.checks.Check;
import ac.grim.grimac.checks.impl.prediction.OffsetHandler;
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
import com.github.retrooper.packetevents.util.EventCreationUtil;
import com.github.retrooper.packetevents.wrapper.handshaking.client.WrapperHandshakingClientHandshake;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerPing;
import io.netty.buffer.Unpooled;
import io.netty.channel.embedded.EmbeddedChannel;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// #2158 replay spike: a whole client session recorded at the wire (fuzz/record_relay.py) fed back through PacketEvents
// and every listener Grim registers on a server, with no server and no client. Clientbound packets go through the
// post-via then the pre-via listeners, serverbound the other way round, like the two passes around ViaVersion. Grim's
// own pings come out of the replayed Grim the same way they did live (same build, same ids), so the recorded pongs
// answer them; any ping the live Grim sent from its tick loop gets sent here right where it shows up in the recording.
class RecordedSessionReplayTest {
    static final Path RECORDING = Path.of(System.getProperty("replay.recording",
            "C:/Users/Aidan/repos/grim-repro/fuzz/out/rec-85d931f.jsonl"));
    static final Pattern LINE = Pattern.compile("\"t\": ([0-9.]+), \"d\": \"(c2s|s2c)\", \"p\": \"([^\"]*)\"");
    static final Pattern MARK = Pattern.compile("FUZZ c1 s(\\d+) (start|end)");

    @Test
    void replaysARecordedSession() throws Exception {
        HeadlessGrimPlayerTest.setUp();
        HeadlessGrimPlayerTest.EVENTS.registerListener(new InternalPacketListener());
        new PacketManager().start();                                   // every listener Grim registers on a server
        Object nativePlayer = new Object();
        User user = new User(new EmbeddedChannel(), ConnectionState.HANDSHAKING, ClientVersion.UNKNOWN, new UserProfile(null, null));

        Map<String, List<String>> flags = new LinkedHashMap<>();
        String segment = "join";
        int packets = 0, pingsCaughtUp = 0, positions = 0;
        long start = System.nanoTime();
        for (String line : Files.readAllLines(RECORDING)) {
            Matcher m = LINE.matcher(line);
            if (!m.find()) continue;
            byte[] bytes = Base64.getDecoder().decode(m.group(3));
            // Grim reads the wall clock (Timer, transaction timing), so play the packets out when they arrived live.
            // A clock seam (#2377) would let this run at full speed instead
            long due = start + (long) (Double.parseDouble(m.group(1)) * 1_000_000);
            long wait = due - System.nanoTime();
            if (wait > 0) Thread.sleep(wait / 1_000_000, (int) (wait % 1_000_000));
            boolean c2s = m.group(2).equals("c2s");
            packets++;
            if (!c2s) {
                Matcher mark = MARK.matcher(new String(bytes, StandardCharsets.ISO_8859_1));
                if (mark.find()) segment = "s" + mark.group(1) + " " + mark.group(2);
            }
            GrimPlayer player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (c2s) {
                PacketReceiveEvent pre = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                HeadlessGrimPlayerTest.EVENTS.callEvent(pre, null, true);
                PacketReceiveEvent post = EventCreationUtil.createReceiveEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                HeadlessGrimPlayerTest.EVENTS.callEvent(post, null, false);
                if (post.hasPostTasks()) post.getPostTasks().forEach(Runnable::run);
                if (player != null && (post.getPacketType() == PacketType.Play.Client.PLAYER_POSITION
                        || post.getPacketType() == PacketType.Play.Client.PLAYER_POSITION_AND_ROTATION)) positions++;
                // the state switches PacketEvents' netty handlers make on a server, all driven by the client's packet
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
                    // the live Grim sent this ping; make sure the replayed Grim has sent it too before it goes by
                    short id = (short) new WrapperPlayServerPing(post).getId();
                    // ids count down from 0, one per sendTransaction(), so issuing until this one exists can't overshoot
                    for (int guard = 0; guard < 64 && id <= 0 && !player.didWeSendThatTrans.contains(id); guard++) {
                        player.sendTransaction();
                        pingsCaughtUp++;
                    }
                    post = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                }
                HeadlessGrimPlayerTest.EVENTS.callEvent(post, null, false);
                PacketSendEvent pre = EventCreationUtil.createSendEvent(user.getChannel(), user, nativePlayer, Unpooled.wrappedBuffer(bytes), true);
                HeadlessGrimPlayerTest.EVENTS.callEvent(pre, null, true);
                // the packet is "on the wire" now: what listeners queued for after the send runs, like the join's addUser
                for (PacketSendEvent sent : List.of(post, pre)) {
                    if (sent.hasPostTasks()) sent.getPostTasks().forEach(Runnable::run);
                    if (sent.hasTasksAfterSend()) sent.getTasksAfterSend().forEach(Runnable::run);
                }
            }
            player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
            if (player != null) {
                for (Check check : player.checkManager.checks.stream().filter(c -> c instanceof Check).map(c -> (Check) c).toList()) {
                    // every check's violations, so anything that flags shows up, not just Simulation
                    double v = check.violations;
                    String key = check.getCheckName();
                    if (v > lastSeen.getOrDefault(player.uuid + key, 0d)) {
                        String detail = check instanceof OffsetHandler
                                ? String.format("%.6f", player.predictedVelocity.vector.distance(player.actualMovement)) : "";
                        flags.computeIfAbsent(segment.replace(" start", "").replace(" end", " after end"), k -> new ArrayList<>())
                                .add(key + (detail.isEmpty() ? "" : " " + detail));
                    }
                    lastSeen.put(player.uuid + key, v);
                }
            }
        }
        GrimPlayer player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
        System.out.println("replayed " + packets + " packets, " + positions + " position packets in play, final state "
                + user.getDecoderState() + "/" + user.getEncoderState() + ", player " + (player != null ? player.getName() : "never created")
                + ", pings sent to catch up with the live Grim: " + pingsCaughtUp
                + ", last transaction received " + (player != null ? player.lastTransactionReceived.get() : -1));
        flags.forEach((seg, list) -> System.out.println("  " + seg + ": " + list));
        System.out.println("listener warnings: " + HeadlessGrimPlayerTest.WARNINGS.size());
        HeadlessGrimPlayerTest.WARNINGS.stream().limit(6).forEach(w -> System.out.println("  " + w));
    }

    static final Map<String, Double> lastSeen = new LinkedHashMap<>();
}
