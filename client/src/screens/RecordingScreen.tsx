import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, FlatList, Alert } from 'react-native';
import { audioRecorder } from '../services/audioRecorder';
import { transferService } from '../services/transfer';
import { storage } from '../storage';
import { Course, RecordingSession, Chunk, ConnectionState, PCConfig } from '../types';
import { QRPairingScreen } from './QRPairingScreen';

const CONNECTION_LABELS: Record<ConnectionState, { color: string; text: string }> = {
  unpaired: { color: '#64748B', text: 'Not paired' },
  searching: { color: '#F59E0B', text: 'Looking for PC...' },
  connected: { color: '#22C55E', text: 'Connected' },
  unreachable: { color: '#F59E0B', text: 'PC offline · uploads queued' },
  unauthorized: { color: '#EF4444', text: 'Re-pair needed' },
};

export const RecordingScreen: React.FC = () => {
  const [courses, setCourses] = useState<Course[]>([]);
  const [selectedCourseId, setSelectedCourseId] = useState<string>('');
  const [sessions, setSessions] = useState<RecordingSession[]>([]);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [showPairing, setShowPairing] = useState(false);
  const [pcConfig, setPcConfig] = useState<PCConfig | null>(null);
  const [connection, setConnection] = useState<{ state: ConnectionState; ip: string | null }>({
    state: 'unpaired',
    ip: null,
  });
  const [recordingState, setRecordingState] = useState(audioRecorder.getState());

  useEffect(() => {
    loadInitialData();
    const unsubscribeRecorder = audioRecorder.subscribe((state) => {
      setRecordingState(state);
    });
    const unsubscribeErrors = audioRecorder.onError((message) => {
      Alert.alert('Recording stopped', `${message}\n\nEverything recorded so far was saved. Tap Resume to continue.`);
      loadSessions();
    });
    const unsubscribeTransfer = transferService.subscribe(() => loadSessions());
    const unsubscribeConnection = transferService.subscribeConnection((state, ip) => setConnection({ state, ip }));

    transferService.initialize().then(async () => {
      setPcConfig(transferService.getPCConfig());
      // Queue anything the recorder finished while the app wasn't running.
      for (const chunk of await storage.findUnrecordedChunks()) {
        if (await storage.addChunk(chunk)) {
          await transferService.enqueueChunk(chunk);
        }
      }
      loadSessions();
    });

    return () => {
      unsubscribeRecorder();
      unsubscribeErrors();
      unsubscribeTransfer();
      unsubscribeConnection();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadInitialData = async () => {
    const loadedCourses = await storage.getCourses();
    setCourses(loadedCourses);
    if (loadedCourses.length > 0) {
      setSelectedCourseId((current) => current || loadedCourses[0].id);
    }
    await loadSessions();
  };

  const loadSessions = async () => {
    const [loadedSessions, loadedChunks] = await Promise.all([storage.getSessions(), storage.getChunks()]);
    setSessions(loadedSessions.sort((a, b) => b.startedAt - a.startedAt));
    setChunks(loadedChunks);
  };

  const runAction = async (title: string, action: () => Promise<unknown>) => {
    try {
      await action();
    } catch (error: any) {
      Alert.alert(title, error?.message ?? String(error));
    } finally {
      loadSessions();
    }
  };

  const handleStartRecording = () => {
    if (!selectedCourseId) {
      Alert.alert('Select Course', 'Please select a course before recording');
      return;
    }
    runAction('Recording Failed', () => audioRecorder.startRecording(selectedCourseId));
  };

  const handlePaired = (config: PCConfig) => {
    setPcConfig(config);
    setShowPairing(false);
  };

  const formatDuration = (ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const mins = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'recording': return '#EF4444';
      case 'paused': return '#F59E0B';
      case 'completed': return '#22C55E';
      case 'failed': return '#EF4444';
      default: return '#64748B';
    }
  };

  const getChunkStatusText = (sessionChunks: Chunk[]) => {
    const synced = sessionChunks.filter(c => c.status === 'synced').length;
    const pending = sessionChunks.filter(c => c.status === 'pending' || c.status === 'uploading').length;
    const failed = sessionChunks.filter(c => c.status === 'failed').length;
    return `${synced}/${sessionChunks.length} synced${pending ? `, ${pending} pending` : ''}${failed ? `, ${failed} failed` : ''}`;
  };

  const renderSession = ({ item }: { item: RecordingSession }) => {
    const course = courses.find(c => c.id === item.courseId);
    const sessionChunks = chunks.filter(c => c.sessionId === item.id);
    const lastError = sessionChunks.find(c => c.status !== 'synced' && c.error)?.error;
    return (
      <View style={styles.sessionCard}>
        <View style={styles.sessionHeader}>
          <View style={styles.courseBadge}><View style={[styles.colorDot, { backgroundColor: course?.color }]} /></View>
          <View style={styles.sessionInfo}>
            <Text style={styles.courseName}>{course?.name || item.courseId}</Text>
            <Text style={styles.sessionMeta}>
              {new Date(item.startedAt).toLocaleString()} · {formatDuration(item.duration || 0)}
            </Text>
          </View>
          <View style={[styles.statusBadge, { backgroundColor: getStatusColor(item.status) }]}>
            <Text style={styles.statusText}>{item.status.toUpperCase()}</Text>
          </View>
        </View>
        <Text style={styles.chunkStatus}>{getChunkStatusText(sessionChunks)}</Text>
        {lastError ? <Text style={styles.chunkError} numberOfLines={2}>Last upload error: {lastError}</Text> : null}
      </View>
    );
  };

  if (showPairing) {
    return <QRPairingScreen onPaired={handlePaired} onCancel={() => setShowPairing(false)} />;
  }

  const connectionLabel = CONNECTION_LABELS[connection.state];

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Lecture Capture</Text>
        {pcConfig ? (
          <TouchableOpacity
            style={styles.pcStatus}
            onPress={() => transferService.retryNow()}
            onLongPress={() => setShowPairing(true)}
          >
            <View style={[styles.pcDot, { backgroundColor: connectionLabel.color }]} />
            <Text style={styles.pcText}>
              {connection.state === 'connected' && connection.ip ? connection.ip : connectionLabel.text}
            </Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity style={styles.pairButton} onPress={() => setShowPairing(true)}>
            <Text style={styles.pairButtonText}>Pair PC</Text>
          </TouchableOpacity>
        )}
      </View>
      {pcConfig && connection.state === 'unauthorized' ? (
        <TouchableOpacity style={styles.banner} onPress={() => setShowPairing(true)}>
          <Text style={styles.bannerText}>The PC rejected this phone. Tap to pair again.</Text>
        </TouchableOpacity>
      ) : null}

      <View style={styles.courseSelector}>
        <Text style={styles.sectionLabel}>Course</Text>
        <View style={styles.courseList}>
          {courses.map(course => (
            <TouchableOpacity
              key={course.id}
              style={[
                styles.courseOption,
                selectedCourseId === course.id && styles.courseOptionSelected,
                { borderColor: course.color }
              ]}
              onPress={() => setSelectedCourseId(course.id)}
              disabled={recordingState.sessionId !== null}
            >
              <View style={[styles.courseColor, { backgroundColor: course.color }]} />
              <Text style={[
                styles.courseOptionText,
                selectedCourseId === course.id && styles.courseOptionTextSelected
              ]}>
                {course.name}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      <View style={styles.recorderSection}>
        {recordingState.isRecording ? (
          <View style={styles.recordingActive}>
            <View style={styles.timerContainer}>
              <Text style={styles.timer}>{formatDuration(recordingState.currentTime)}</Text>
              <Text style={styles.chunkInfo}>Chunk {recordingState.currentChunk + 1}</Text>
            </View>
            <View style={styles.controls}>
              <TouchableOpacity style={styles.controlButton} onPress={() => runAction('Pause Failed', () => audioRecorder.pauseRecording())}>
                <Text style={styles.controlButtonText}>⏸ Pause</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.controlButton, styles.controlButtonStop]} onPress={() => runAction('Stop Failed', () => audioRecorder.stopRecording())}>
                <Text style={styles.controlButtonText}>■ Stop</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : recordingState.sessionId ? (
          <View style={styles.recordingPaused}>
            <Text style={styles.pausedText}>Recording Paused</Text>
            <Text style={styles.pausedMeta}>{formatDuration(recordingState.currentTime)} · Chunk {recordingState.currentChunk + 1}</Text>
            <View style={styles.controls}>
              <TouchableOpacity style={styles.controlButton} onPress={() => runAction('Resume Failed', () => audioRecorder.resumeRecording())}>
                <Text style={styles.controlButtonText}>▶ Resume</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.controlButton, styles.controlButtonStop]} onPress={() => runAction('Stop Failed', () => audioRecorder.stopRecording())}>
                <Text style={styles.controlButtonText}>■ Stop</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : (
          <TouchableOpacity style={styles.recordButton} onPress={handleStartRecording} disabled={!selectedCourseId}>
            <View style={styles.recordButtonInner} />
            <Text style={styles.recordButtonText}>● RECORD</Text>
            {!pcConfig ? <Text style={styles.recordHint}>Recordings are kept on the phone until you pair a PC</Text> : null}
          </TouchableOpacity>
        )}
      </View>

      <View style={styles.sessionsSection}>
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionLabel}>Recent Sessions</Text>
        </View>
        {sessions.length === 0 ? (
          <View style={styles.emptyState}>
            <Text style={styles.emptyText}>No recordings yet</Text>
            <Text style={styles.emptySubtext}>Pair your PC and start recording</Text>
          </View>
        ) : (
          <FlatList
            data={sessions}
            renderItem={renderSession}
            keyExtractor={item => item.id}
            extraData={chunks}
            contentContainerStyle={styles.sessionsList}
            showsVerticalScrollIndicator={false}
          />
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0F172A' },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 20 },
  headerTitle: { fontSize: 28, fontWeight: '700', color: '#F8FAFC' },
  pcStatus: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#1E293B', paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20, maxWidth: 190 },
  pcDot: { width: 8, height: 8, borderRadius: 4 },
  pcText: { color: '#94A3B8', fontSize: 13, fontFamily: 'monospace', flexShrink: 1 },
  pairButton: { backgroundColor: '#2563EB', paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20 },
  pairButtonText: { color: '#F8FAFC', fontSize: 14, fontWeight: '600' },
  banner: { marginHorizontal: 20, marginBottom: 12, padding: 12, borderRadius: 8, backgroundColor: '#7F1D1D' },
  bannerText: { color: '#FEE2E2', fontSize: 14 },
  courseSelector: { paddingHorizontal: 20, marginBottom: 16 },
  sectionLabel: { color: '#94A3B8', fontSize: 13, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 },
  courseList: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  courseOption: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, paddingVertical: 10, borderWidth: 2, borderRadius: 20, backgroundColor: '#1E293B' },
  courseOptionSelected: { backgroundColor: 'rgba(37, 99, 235, 0.2)' },
  courseColor: { width: 10, height: 10, borderRadius: 5 },
  courseOptionText: { color: '#E2E8F0', fontSize: 14, fontWeight: '500' },
  courseOptionTextSelected: { color: '#2563EB' },
  recorderSection: { paddingHorizontal: 20, marginBottom: 24 },
  recordingActive: { alignItems: 'center' },
  recordingPaused: { alignItems: 'center' },
  timerContainer: { alignItems: 'center', marginBottom: 24 },
  timer: { fontSize: 64, fontWeight: '700', color: '#F8FAFC', fontFamily: 'monospace' },
  chunkInfo: { color: '#64748B', fontSize: 14, marginTop: 4 },
  pausedText: { fontSize: 24, fontWeight: '600', color: '#F59E0B', marginBottom: 4 },
  pausedMeta: { color: '#64748B', fontSize: 14, marginBottom: 24 },
  controls: { flexDirection: 'row', gap: 16 },
  controlButton: { flex: 1, paddingVertical: 16, borderRadius: 12, alignItems: 'center', backgroundColor: '#1E293B', borderWidth: 1, borderColor: '#334155' },
  controlButtonStop: { backgroundColor: '#7F1D1D', borderColor: '#EF4444' },
  controlButtonText: { color: '#F8FAFC', fontSize: 16, fontWeight: '600' },
  recordButton: { alignItems: 'center', paddingVertical: 8 },
  recordButtonInner: { width: 80, height: 80, borderRadius: 40, backgroundColor: '#EF4444', borderWidth: 4, borderColor: '#F8FAFC' },
  recordButtonText: { color: '#F8FAFC', fontSize: 18, fontWeight: '700', marginTop: 12, letterSpacing: 2 },
  recordHint: { color: '#64748B', fontSize: 13, marginTop: 8, textAlign: 'center' },
  sessionsSection: { flex: 1, paddingHorizontal: 16 },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  sessionsList: { paddingBottom: 20 },
  sessionCard: { backgroundColor: '#1E293B', borderRadius: 12, padding: 16, marginBottom: 12, borderWidth: 1, borderColor: '#334155' },
  sessionHeader: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 8 },
  courseBadge: { flex: 0 },
  colorDot: { width: 12, height: 12, borderRadius: 6 },
  sessionInfo: { flex: 1 },
  courseName: { color: '#F8FAFC', fontSize: 16, fontWeight: '600' },
  sessionMeta: { color: '#64748B', fontSize: 12, marginTop: 2 },
  statusBadge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  statusText: { color: '#F8FAFC', fontSize: 11, fontWeight: '600' },
  chunkStatus: { color: '#94A3B8', fontSize: 12 },
  chunkError: { color: '#FCA5A5', fontSize: 12, marginTop: 4 },
  emptyState: { flex: 1, justifyContent: 'center', alignItems: 'center', paddingTop: 40 },
  emptyText: { color: '#64748B', fontSize: 18, fontWeight: '500' },
  emptySubtext: { color: '#475569', fontSize: 14, marginTop: 4 },
});
