"""
Phase 4 Complete Voice Mode Implementation
This is the replacement code for the voice mode section in app.py

Copy this code and replace the voice mode else block in show_interview()
"""

# Replace the else block starting around line 338 with this:

else:
    # VOICE MODE - Phase 4: Complete TTS + Recording + STT flow
    st.subheader("🎙️ Voice Interview Mode")

    # Initialize audio cache and recording state
    if "audio_cache" not in st.session_state:
        st.session_state.audio_cache = {}

    recording_key = f"recording_{current_idx}_{question_id}"
    transcript_key = f"transcript_{current_idx}_{question_id}"

    if recording_key not in st.session_state:
        st.session_state[recording_key] = {"frames": [], "status": "ready"}
    if transcript_key not in st.session_state:
        st.session_state[transcript_key] = st.session_state.submitted_answers.get(current_idx, "")

    # === PHASE 2: TTS - Play Question ===
    col_tts, col_record = st.columns(2)

    with col_tts:
        if st.button("🔊 Play Question", key=f"play_q_{current_idx}"):
            try:
                with st.spinner("Generating speech..."):
                    cache_key = f"q_{question_id}"
                    if cache_key not in st.session_state.audio_cache:
                        result = speak_question(question_id)
                        audio_base64 = result.get("audio_base64")
                        st.session_state.audio_cache[cache_key] = audio_base64
                    else:
                        audio_base64 = st.session_state.audio_cache[cache_key]

                    if audio_base64:
                        import base64
                        audio_bytes = base64.b64decode(audio_base64)
                        st.audio(audio_bytes, format="audio/mp3")
                        st.success("✓ Question audio ready")
                    else:
                        st.error("Could not generate audio.")

            except requests.RequestException as e:
                error_msg = str(e)
                try:
                    if hasattr(e, 'response') and e.response is not None:
                        response_data = e.response.json()
                        if 'detail' in response_data:
                            error_msg = response_data['detail']
                except:
                    pass
                st.warning(f"Audio unavailable: {error_msg}")

    # === PHASE 3/4: Recording ===
    with col_record:
        try:
            from streamlit_webrtc import webrtc_streamer, WebRtcMode, RTCConfiguration
            from audio_utils import convert_audio_frames_to_wav

            # WebRTC configuration
            rtc_configuration = RTCConfiguration(
                {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
            )

            # Recording component
            webrtc_ctx = webrtc_streamer(
                key=f"voice_answer_{current_idx}",
                mode=WebRtcMode.SENDRECV,
                rtc_configuration=rtc_configuration,
                media_stream_constraints={"audio": True, "video": False},
                async_processing=True,
            )

            # Display recording status
            if webrtc_ctx.state.playing:
                st.info("🎙️ **Recording in progress...** Speak your answer.")
                st.session_state[recording_key]["status"] = "recording"
            else:
                # Recording finished, extract audio
                if webrtc_ctx.audio_processor and webrtc_ctx.audio_processor.frames:
                    try:
                        # Get audio frames
                        frames = list(webrtc_ctx.audio_processor.frames)

                        if frames:
                            st.session_state[recording_key]["frames"] = frames
                            st.session_state[recording_key]["status"] = "ready_to_transcribe"
                            st.success("✓ Recording complete")
                        else:
                            st.warning("No audio was recorded. Please try again.")
                            st.session_state[recording_key]["status"] = "ready"

                    except Exception as e:
                        st.error(f"Error processing audio: {str(e)}")
                        st.session_state[recording_key]["status"] = "ready"

        except ImportError:
            st.warning("⚠️ Microphone library not available.")
            st.info("Please use **Text mode** to answer this question.")
        except Exception as e:
            st.error(f"❌ Microphone unavailable: {str(e)}")
            st.info("You can type your answer manually below.")

    # === PHASE 4: Transcription ===
    st.markdown("**Transcript:**")

    # Transcribe button (if recording available)
    if st.session_state[recording_key].get("frames"):
        col_trans_btn, col_trans_info = st.columns([1, 3])

        with col_trans_btn:
            if st.button("📝 Transcribe", key=f"transcribe_{current_idx}"):
                try:
                    with st.spinner("Transcribing audio..."):
                        # Convert frames to WAV
                        wav_bytes = convert_audio_frames_to_wav(
                            st.session_state[recording_key].get("frames", [])
                        )

                        if not wav_bytes:
                            raise Exception("Could not convert audio to WAV format")

                        # Send to backend STT
                        files = {"file": ("answer.wav", wav_bytes, "audio/wav")}
                        response = requests.post(
                            f"{BACKEND_URL}/api/audio/speech-to-text",
                            files=files
                        )
                        response.raise_for_status()

                        result = response.json()
                        transcript_text = result.get("text", "")

                        if transcript_text.strip():
                            st.session_state[transcript_key] = transcript_text
                            st.success("✓ Transcription complete")
                            st.session_state[recording_key]["status"] = "transcribed"
                        else:
                            st.warning("No speech was detected in the recording.")

                except requests.RequestException as e:
                    error_msg = str(e)
                    try:
                        if hasattr(e, 'response') and e.response is not None:
                            response_data = e.response.json()
                            if 'detail' in response_data:
                                error_msg = response_data['detail']
                    except:
                        pass
                    st.error(f"Transcription failed: {error_msg}")
                    st.info("You can type or edit the transcript manually below.")

        with col_trans_info:
            st.caption("Click 'Transcribe' to convert your recording to text")

    # Editable transcript area
    transcript = st.text_area(
        "Your answer (edit if needed before submitting)",
        value=st.session_state[transcript_key],
        height=100,
        key=f"transcript_text_{current_idx}",
        placeholder="Transcribed text will appear here, or type/paste your answer"
    )

    # Update session state
    st.session_state[transcript_key] = transcript

    # Phase status info
    st.info(
        "📌 **Voice Interview Status:**\n"
        "- ✓ Phase 2: Click 🔊 to hear the question\n"
        "- ✓ Phase 3: Microphone records your answer\n"
        "- ✓ Phase 4: Click 📝 to transcribe or edit manually\n"
        "- ✓ Then click Submit Answer"
    )

    # Final answer text for submission
    answer_text = transcript if transcript.strip() else ""
