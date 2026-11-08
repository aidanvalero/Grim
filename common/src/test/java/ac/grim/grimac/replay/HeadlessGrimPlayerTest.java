package ac.grim.grimac.replay;

import ac.grim.grimac.GrimAPI;
import ac.grim.grimac.api.plugin.GrimPlugin;
import ac.grim.grimac.manager.config.ConfigManagerFileImpl;
import ac.grim.grimac.manager.datastore.DataStoreLifecycle;
import ac.grim.grimac.platform.api.PlatformLoader;
import ac.grim.grimac.checks.impl.prediction.OffsetHandler;
import ac.grim.grimac.events.packets.CheckManagerListener;
import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.netty.buffer.ByteBufHelper;
import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.util.EventCreationUtil;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientPlayerPosition;
import io.netty.buffer.ByteBuf;
import io.netty.buffer.Unpooled;
import ac.grim.grimac.player.GrimPlayer;
import ac.grim.grimac.utils.anticheat.update.PositionUpdate;
import ac.grim.grimac.utils.chunks.Column;
import ac.grim.grimac.utils.latency.CompensatedWorld;
import com.github.retrooper.packetevents.protocol.world.chunk.BaseChunk;
import com.github.retrooper.packetevents.util.Vector3d;
import com.github.retrooper.packetevents.PacketEvents;
import com.github.retrooper.packetevents.PacketEventsAPI;
import com.github.retrooper.packetevents.manager.server.ServerManager;
import com.github.retrooper.packetevents.manager.server.ServerVersion;
import com.github.retrooper.packetevents.protocol.ConnectionState;
import com.github.retrooper.packetevents.protocol.player.ClientVersion;
import com.github.retrooper.packetevents.protocol.player.User;
import com.github.retrooper.packetevents.protocol.player.UserProfile;
import com.github.retrooper.packetevents.settings.PacketEventsSettings;
import io.github.retrooper.packetevents.impl.netty.NettyManagerImpl;
import io.netty.channel.embedded.EmbeddedChannel;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.nio.file.Files;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.RETURNS_DEEP_STUBS;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// Spike for #2158: can a real GrimPlayer (every check, the prediction engine) be built without a server?
class HeadlessGrimPlayerTest {

    private static boolean ready;

    static synchronized void setUp() throws Exception {
        if (ready) return;
        PacketEventsAPI<?> api = mock(PacketEventsAPI.class, RETURNS_DEEP_STUBS);
        ServerManager server = mock(ServerManager.class);
        when(server.getVersion()).thenReturn(ServerVersion.V_26_3);
        when(api.getServerManager()).thenReturn(server);
        when(api.getSettings()).thenReturn(new PacketEventsSettings());
        when(api.getNettyManager()).thenReturn(new NettyManagerImpl()); // real buffers, PacketEvents' static data writes NBT into them
        PacketEvents.setAPI(api);
        // the platform: so far only the plugin's data folder, where Grim writes and reads its default config files
        PlatformLoader loader = mock(PlatformLoader.class, RETURNS_DEEP_STUBS);
        GrimPlugin plugin = mock(GrimPlugin.class, RETURNS_DEEP_STUBS);
        when(plugin.getDataFolder()).thenReturn(Files.createTempDirectory("grim-replay").toFile());
        when(loader.getPlugin()).thenReturn(plugin);
        Field field = GrimAPI.class.getDeclaredField("loader");
        field.setAccessible(true);
        field.set(GrimAPI.INSTANCE, loader);
        // what GrimAPI.load() builds next; constructed only, nothing started, so no database
        Field lifecycle = GrimAPI.class.getDeclaredField("dataStoreLifecycle");
        lifecycle.setAccessible(true);
        lifecycle.set(GrimAPI.INSTANCE, new DataStoreLifecycle(plugin, GrimAPI.INSTANCE.getBackendRegistry()));
        ConfigManagerFileImpl config = new ConfigManagerFileImpl();
        config.reload();                                   // the stock config a fresh server starts with
        GrimAPI.INSTANCE.getConfigManager().load(config);
        // verbose tags ({offset} etc.) are registered in VerboseCodecs' static init, which a server's startup triggers
        Class.forName("ac.grim.grimac.checks.impl.verbose.VerboseCodecs", true, HeadlessGrimPlayerTest.class.getClassLoader());
        ready = true;
    }

    static GrimPlayer newPlayer() throws Exception {
        setUp();
        User user = new User(new EmbeddedChannel(), ConnectionState.PLAY, ClientVersion.V_26_3,
                new UserProfile(UUID.randomUUID(), "Replay"));
        return new GrimPlayer(user);
    }

    // what CheckManagerListener does with a position packet before the checks see it
    static void move(GrimPlayer player, double x, double y, double z, boolean teleport) {
        PositionUpdate update = new PositionUpdate(new Vector3d(player.x, player.y, player.z), new Vector3d(x, y, z), false, null, null, teleport);
        player.lastX = player.x;
        player.lastY = player.y;
        player.lastZ = player.z;
        player.x = x;
        player.y = y;
        player.z = z;
        player.checkManager.onPositionUpdate(update);
    }

    @Test
    void runsThePredictionEngine() throws Exception {
        GrimPlayer player = newPlayer();
        // 3x3 chunks of air around the origin the client already has (sent before the last transaction it answered)
        for (int cx = -1; cx <= 1; cx++) {
            for (int cz = -1; cz <= 1; cz++) {
                player.compensatedWorld.chunks.put(CompensatedWorld.chunkPositionToLong(cx, cz), new Column(cx, cz, new BaseChunk[16], new long[16], -1));
            }
        }
        player.getSetbackTeleportUtil().hasAcceptedSpawnTeleport = true; // past "downloading terrain"
        move(player, 0.5, 100, 0.5, true);
        OffsetHandler simulation = (OffsetHandler) player.checkManager.checks.stream().filter(c -> c instanceof OffsetHandler).findFirst().orElseThrow();
        // vanilla in the air: move by the velocity, then gravity and drag. Tick 12 gets sent 0.1 higher than the client got
        double y = 100, vy = 0;
        StringBuilder log = new StringBuilder();
        double worstClean = 0, bent = 0;
        for (int tick = 0; tick < 20; tick++) {
            y += vy;
            vy = (vy - 0.08) * 0.98;
            boolean bend = tick == 12;
            double before = simulation.violations;
            move(player, 0.5, bend ? y + 0.1 : y, 0.5, false);
            double offset = player.predictedVelocity.vector.distance(player.actualMovement);
            log.append(String.format("tick %2d y %.6f predicted dy %.9f actual dy %.9f offset %.3e%s%n", tick, player.y,
                    player.predictedVelocity.vector.getY(), player.actualMovement.getY(), offset, simulation.violations > before ? "  <- Simulation flagged" : ""));
            if (bend) {
                bent = offset;
                break;
            }
            worstClean = Math.max(worstClean, offset);
        }
        System.out.print(log);
        System.out.println("worst clean offset " + worstClean + ", bent tick offset " + bent + ", Simulation violations " + simulation.violations);
        assertTrue(worstClean < 1e-6, "clean vanilla ticks predicted exactly");
        assertTrue(bent > 0.05 && simulation.violations > 0, "the bent tick flags Simulation");
    }

    // a serverbound position packet as the bytes a 26.3 client puts on the wire, through PacketEvents into Grim's own listener
    static void receivePosition(CheckManagerListener listener, User user, double x, double y, double z, boolean onGround) throws Exception {
        ByteBuf buf = Unpooled.buffer();
        ByteBufHelper.writeVarInt(buf, PacketType.Play.Client.PLAYER_POSITION.getId(ClientVersion.V_26_3));
        WrapperPlayClientPlayerPosition wrapper = new WrapperPlayClientPlayerPosition(new Vector3d(x, y, z), onGround);
        wrapper.setBuffer(buf);
        wrapper.write();
        PacketReceiveEvent event = EventCreationUtil.createReceiveEvent(user.getChannel(), user, null, buf, true);
        listener.onPacketReceive(event);
    }

    @Test
    void runsFromRawPackets() throws Exception {
        setUp();
        User user = new User(new EmbeddedChannel(), ConnectionState.PLAY, ClientVersion.V_26_3, new UserProfile(UUID.randomUUID(), "Packets"));
        GrimAPI.INSTANCE.getPlayerDataManager().addUser(user);           // what a join does
        GrimPlayer player = GrimAPI.INSTANCE.getPlayerDataManager().getPlayer(user);
        for (int cx = -1; cx <= 1; cx++) {
            for (int cz = -1; cz <= 1; cz++) {
                player.compensatedWorld.chunks.put(CompensatedWorld.chunkPositionToLong(cx, cz), new Column(cx, cz, new BaseChunk[16], new long[16], -1));
            }
        }
        player.getSetbackTeleportUtil().hasAcceptedSpawnTeleport = true;
        move(player, 0.5, 100, 0.5, true);                               // where the spawn teleport put it
        OffsetHandler simulation = (OffsetHandler) player.checkManager.checks.stream().filter(c -> c instanceof OffsetHandler).findFirst().orElseThrow();
        CheckManagerListener listener = new CheckManagerListener();
        double y = 100, vy = 0, worstClean = 0, bent = 0;
        StringBuilder log = new StringBuilder();
        for (int tick = 0; tick < 20; tick++) {
            y += vy;
            vy = (vy - 0.08F) * 0.98F;                                     // vanilla's float gravity and drag this time
            boolean bend = tick == 12;
            double before = simulation.violations;
            receivePosition(listener, user, 0.5, bend ? y + 0.1 : y, 0.5, false);
            double offset = player.predictedVelocity.vector.distance(player.actualMovement);
            log.append(String.format("packet tick %2d y %.6f offset %.3e%s%n", tick, player.y, offset,
                    simulation.violations > before ? "  <- Simulation flagged" : ""));
            if (bend) {
                bent = offset;
                break;
            }
            worstClean = Math.max(worstClean, offset);
        }
        System.out.print(log);
        System.out.println("raw packets: worst clean offset " + worstClean + ", bent tick offset " + bent + ", Simulation violations " + simulation.violations);
        assertTrue(worstClean < 1e-6, "clean vanilla ticks predicted exactly");
        assertTrue(bent > 0.05 && simulation.violations > 0, "the bent tick flags Simulation");
    }

    @Test
    void buildsWithoutAServer() throws Exception {
        GrimPlayer player = newPlayer();
        assertNotNull(player.checkManager);
        assertNotNull(player.movementCheckRunner);
        System.out.println("built a GrimPlayer headless: " + player.checkManager.checks.size() + " checks");
    }
}
