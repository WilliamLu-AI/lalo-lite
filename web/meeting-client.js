import {Room, RoomEvent, DisconnectReason} from 'livekit-client';

// Transport adapter only: the existing reader owns rendering and playback.
export function createMeetingClient({onEvent, onParticipants = () => {}, onAudioTrack = () => {}, roomFactory = () => new Room(), fetcher = fetch}) {
  let room, role, sequence = 0, serial = 0, handshake;
  let identity, floor, microphone = false, micGeneration = 0;
  let memberCredential, memberRole;
  const prefix = globalThis.crypto.randomUUID();
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  const remoteAudio = new Map();
  const publishRoster = () => onParticipants({
    aiConnected: !!room?.remoteParticipants?.has('lalo-ai'),
    people: room && identity ? [{identity, self:true}, ...Array.from(room.remoteParticipants?.values() || [])
      .filter(p => p.identity !== 'lalo-ai').map(p => ({identity:p.identity, ...(p.name ? {name:p.name} : {}), self:false}))] : [],
  });
  const client = {
    async connect(credential) {
      memberCredential = credential;
      const response = await fetcher('/api/meetings/room', {method: 'POST', cache: 'no-store',
        headers: {Authorization: 'Bearer ' + credential}});
      if (!response.ok) throw new Error('Could not join the meeting');
      const connection = await response.json();
      identity = connection.identity;
      room = roomFactory();
      room.on(RoomEvent.ParticipantConnected, publishRoster);
      room.on(RoomEvent.ParticipantDisconnected, publishRoster);
      room.on(RoomEvent.Disconnected, reason => {
        clearInterval(handshake);
        floor = undefined;
        client.stopMicrophone(false).catch(() => {});
        onEvent({type: reason === DisconnectReason.ROOM_DELETED ? 'meeting_ended' : 'meeting_disconnected'});
      });
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        if (track.kind !== 'audio') return;
        remoteAudio.set(track, participant.identity);
        onAudioTrack(track, participant.identity, true);
      });
      room.on(RoomEvent.TrackUnsubscribed, track => {
        if (!remoteAudio.has(track)) return;
        onAudioTrack(track, remoteAudio.get(track), false);
        remoteAudio.delete(track);
      });
      room.on(RoomEvent.DataReceived, (bytes, participant, kind, topic) => {
        if (topic !== 'lalo-meeting-events' || participant?.identity !== 'lalo-ai') return;
        let event;
        try { event = JSON.parse(decoder.decode(bytes)); } catch { return; }
        if (!Number.isSafeInteger(event.sequence) || event.sequence <= sequence) return;
        sequence = event.sequence;
        if (event.type === 'state') {
          role = event.snapshot?.role; floor = event.snapshot?.floor; clearInterval(handshake);
          memberRole = role;
          if (microphone && role !== 'host' && floor !== identity) client.stopMicrophone(false).catch(error => onEvent({type:'error',message:error.message}));
        }
        onEvent(event);
        if (event.type === 'meeting_ended') client.close().catch(() => {});
      });
      await room.connect(connection.url, connection.token);
      publishRoster();
      let sync = encoder.encode(JSON.stringify({type: 'sync', request_id: prefix + '-ready'}));
      let deadline = Date.now() + 10000;
      const requestState = async () => {
        if (Date.now() > deadline) {
          clearInterval(handshake);
          onEvent({type: 'error', message: 'Meeting state synchronization timed out'});
          return;
        }
        try { await room.localParticipant.publishData(sync, {reliable: true, topic: 'lalo-meeting-command'}); }
        catch (error) { clearInterval(handshake); onEvent({type: 'error', message: error.message}); }
      };
      handshake = setInterval(requestState, 300);
      const reconnecting = () => {
        clearInterval(handshake);
        floor = undefined;
        client.stopMicrophone(false).catch(() => {});
        onEvent({type:'meeting_reconnecting'});
      };
      room.on(RoomEvent.Reconnecting, reconnecting);
      room.on(RoomEvent.SignalReconnecting, reconnecting);
      room.on(RoomEvent.Reconnected, () => {
        sync = encoder.encode(JSON.stringify({type: 'sync', recovering: true,
          request_id: prefix + '-reconnected-' + (++serial)}));
        deadline = Date.now() + 10000;
        clearInterval(handshake);
        handshake = setInterval(requestState, 300);
        requestState();
      });
      await requestState();
      return connection.identity;
    },
    async command(message) {
      if (!room) throw new Error('Meeting is not connected');
      if (['sentence_played', 'playback_done'].includes(message.type) && role !== 'host') return;
      await room.localParticipant.publishData(encoder.encode(JSON.stringify({
        ...message, request_id: prefix + '-' + (++serial),
      })), {reliable: true, topic: 'lalo-meeting-command'});
    },
    async startMicrophone({deviceId} = {}) {
      if (!room || !identity || (role !== 'host' && floor !== identity)) throw new Error('Speaking floor is not granted');
      if (microphone) return;
      const generation = ++micGeneration;
      microphone = true;
      try {
        await room.localParticipant.setMicrophoneEnabled(true,
          {echoCancellation:true, noiseSuppression:true, autoGainControl:true, ...(deviceId ? {deviceId} : {})}, {stopMicTrackOnMute:true});
        if (generation !== micGeneration || (role !== 'host' && floor !== identity)) await room.localParticipant.setMicrophoneEnabled(false);
      } catch (error) {microphone = false; throw error;}
    },
    async enableAudio() {
      if (room) await room.startAudio();
    },
    async end() {
      if (memberRole !== 'host') throw new Error('Only the host can end the meeting');
      const response = await fetcher('/api/meetings/end', {method:'POST', cache:'no-store',
        headers:{Authorization:'Bearer ' + memberCredential}});
      if (!response.ok) throw new Error('Could not end the meeting');
      await client.close();
      onEvent({type:'meeting_ended'});
    },
    async stopMicrophone(submit = true) {
      ++micGeneration;
      if (!room || !microphone) return;
      microphone = false;
      await room.localParticipant.setMicrophoneEnabled(false);
      if (submit && floor === identity) await client.command({type:'speech_end'});
    },
    async close() {
      clearInterval(handshake);
      await client.stopMicrophone(false);
      if (room) await room.disconnect();
      for (const [track, speaker] of remoteAudio) onAudioTrack(track, speaker, false);
      remoteAudio.clear();
      room = null;
      publishRoster();
      role = undefined;
    },
  };
  return client;
}
