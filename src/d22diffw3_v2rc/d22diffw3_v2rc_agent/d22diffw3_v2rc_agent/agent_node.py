from pprint import pprint
import time
import requests
import rclpy
from rclpy.node import Node
import asyncio
import threading
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langchain_core.callbacks import BaseCallbackHandler
from sensor_msgs.msg import CompressedImage
import logging
import base64
import sys
import cv2
from cv_bridge import CvBridge
from pydantic import PrivateAttr
from typing import Callable, Optional, List, Any
import json
import aiohttp
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import BaseMessage
from pydantic import Field
from concurrent.futures import ThreadPoolExecutor
import signal
import readline # dont remove it need for clear input()
import copy
import os  # <--- Needed for os.system('stty sane')

# === Debugging ===
def attach_debugger():
    import debugpy
    debugpy.configure(python=sys.executable)
    debugpy.listen(("0.0.0.0", 5678))
    print("⏳ Waiting for debugger to attach on port 5678...")
    # debugpy.wait_for_client()
    debugpy.trace_this_thread(True)
    debugpy.debug_this_thread()
    print('DEBUG attached.')
    debugpy.breakpoint()
# attach_debugger()
 

logging.basicConfig(level=logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langgraph").setLevel(logging.WARNING)

debug_level = logging.WARNING
VISUAL_LINE_LENGTH = 80 

def _build_system_prompt(
    lang: str,
    tool_names: str,
    use_camera_every_tick: bool,
    active_camera_names: List[str],
    specific_system_prompt_override: Optional[str] = None
) -> str:
    """Builds a language-aware system prompt for the agent."""

    cam_list_str = ", ".join(active_camera_names) if active_camera_names else "none"

    # Language-specific strings
    if lang == "en":
        lang_suffix = "\n❗ Respond ONLY in English."
    else:  # ru
        lang_suffix = ""

    prefix = f"Ты — helpfull ассистент помогающий управлять роботом с камерами: {cam_list_str}." if use_camera_every_tick else \
                "Ты — helpfull ассистент помогающий управлять роботом."
    cam_note = (
        f""
    ) if use_camera_every_tick else (
        "Изображения НЕ прикладываются автоматически. "
        "Однако, если ты вызвал инструмент (например, analyze_previously_received_image), "
        "и он вернул изображение, то это изображение будет приложено к следующему твоему запросу "
        "и считается снятым камерой спереди (front view)."
    )
    format_instructions = (
        "Используй ТОЛЬКО следующий формат:\n"
        "Thought: <твои рассуждения>\n"
        "Action: <имя_инструмента>\n"
        "Action Input: <аргументы в строгом JSON>\n\n"
        "Если задача завершена обязательно напиши:\n"
        "Final Answer: <ответ>\n\n"
        "Не циклись, давай 1 ответ по формату за одно обращение!\n"
        f"Доступные инструменты: {tool_names}\n"
        "❗ Action Input ДОЛЖЕН быть валидным JSON. Не добавляй пояснений.\n"
    )
    
    # Use override if provided, else use default hardcoded prompt
    if specific_system_prompt_override is not None:
        specific_system_prompt = specific_system_prompt_override
    else:
        specific_system_prompt = (
            'Не нужно подключаться к роботу, робот в симуляции.'
            'Не используй команды движения если пользователь об этом не просил явно!'

            'Left turn - publish_once {"topic": "/diff_drive_controller/cmd_vel", "msg_type": "geometry_msgs/msg/TwistStamped", "msg": {"twist": {"angular": {"z": 1}}}} т.е. z > 0'
            'Right turn - publish_once {"topic": "/diff_drive_controller/cmd_vel", "msg_type": "geometry_msgs/msg/TwistStamped", "msg": {"twist": {"angular": {"z": -1}}}} т.е. z < 0'
            'Forward - publish_once {"topic": "/diff_drive_controller/cmd_vel", "msg_type": "geometry_msgs/msg/TwistStamped", "msg": {"twist": {"linear": {"x": 0.5}}}} т.е. x > 0'
            'Backward - publish_once {"topic": "/diff_drive_controller/cmd_vel", "msg_type": "geometry_msgs/msg/TwistStamped", "msg": {"twist": {"linear": {"x": -0.5}}}} т.е. x < 0'  
        )
    return "".join([format_instructions, prefix, "\n", cam_note, "\n\n", specific_system_prompt, lang_suffix])

class DebugCallback(BaseCallbackHandler):
    if debug_level == logging.DEBUG:
        def on_llm_start(self, serialized, prompts, **kwargs):
            print("\n📥 LLM REQUEST:")
            for p in prompts:
                print(p)

        def on_llm_end(self, response, **kwargs):
            print("\n📤 LLM RESPONSE:")
            print(response.generations[0][0].text)

        def on_tool_end(self, output, **kwargs):
            print(f"📤 Tool result: {output}")

        def on_tool_start(self, serialized, input_str, **kwargs):
            print(f"\n🔧 Starting tool: {serialized['name']}")
            print(f"📥 Input: {input_str}")


class VisionAwareChatOllama(BaseChatModel):
    model: str = Field(description="Model name in Ollama")
    base_url: str = Field(description="Base URL of the Ollama server")
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    system_prompt: Optional[str] = Field(default=None)
    max_history_images: int = Field(default=4, ge=1, description="Max number of image sets in history")
    camera_names: List[str] = Field(default_factory=list, description="Names of active cameras")
    keep_last_tool_messages: int = Field(default=5, ge=0, description="How many recent tool interactions to keep")

    _get_images_b64_func: Callable[[], Optional[List[str]]] = PrivateAttr()
    _last_images_b64: List[List[str]] = PrivateAttr(default_factory=list)

    def __init__(
        self,
        get_images_b64_func: Callable[[], Optional[List[str]]],
        model: str,
        base_url: str,
        temperature: float = 0.0,
        system: Optional[str] = None,
        max_history_images: int = 1,
        camera_names: Optional[List[str]] = None,
        keep_last_tool_messages: int = 5,
        **kwargs
    ):
        super().__init__(
            model=model,
            base_url=base_url.rstrip("/"),
            temperature=temperature,
            system_prompt=system,
            max_history_images=max_history_images,
            camera_names=camera_names or [],
            keep_last_tool_messages=keep_last_tool_messages,
            **kwargs
        )
        self._get_images_b64_func = get_images_b64_func
        self._last_images_b64 = []

    @property
    def _llm_type(self) -> str:
        return "vision_aware_ollama_multicam"

    def _generate(self, messages: List[BaseMessage], **kwargs) -> ChatResult:
        def run_async():
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(self._agenerate(messages, **kwargs))
            finally:
                loop.close()
        with ThreadPoolExecutor() as executor:
            return executor.submit(run_async).result()

    async def _agenerate(self, messages: List[BaseMessage], **kwargs) -> ChatResult:
        current_images = self._get_images_b64_func()

        ollama_messages = []
        system_content = self.system_prompt
        for msg in messages:
            if isinstance(msg, SystemMessage):
                system_content = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
        if system_content:
            ollama_messages.append({"role": "system", "content": system_content})

        for msg in messages:
            if isinstance(msg, SystemMessage):
                continue
            elif isinstance(msg, HumanMessage):
                content = ""
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    texts = [item["text"] for item in msg.content if item.get("type") == "text"]
                    content = "\n".join(texts)
                ollama_messages.append({"role": "user", "content": content})
            elif isinstance(msg, AIMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                ollama_messages.append({"role": "assistant", "content": content})
            elif isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                ollama_messages.append({
                    "role": "user",
                    "content": f"Tool '{msg.name}' returned: {content}"
                })

        # Clean up old tool messages using the parameter
        def clean_tool_messages(messages, keep_last_n):
            assistant_indices = []
            for i in range(len(messages) - 1):
                current = messages[i]
                next_msg = messages[i + 1]
                if (
                    current.get("role") == "assistant"
                    and next_msg.get("role") == "user"
                    and next_msg.get("content", "").startswith("Tool")
                ):
                    assistant_indices.append(i)
            if len(assistant_indices) <= keep_last_n:
                return messages
            to_remove_count = len(assistant_indices) - keep_last_n
            indices_to_remove = set()
            for idx in assistant_indices[:to_remove_count]:
                indices_to_remove.add(idx)
                indices_to_remove.add(idx + 1)
            return [msg for i, msg in enumerate(messages) if i not in indices_to_remove]

        ollama_messages = clean_tool_messages(ollama_messages, keep_last_n=self.keep_last_tool_messages)

        # Add images ONLY if they exist
        if current_images is not None:
            ollama_messages_with_image_reversed = []
            first_human_msg = True
            for msg in reversed(ollama_messages):
                if msg["role"] == "user":
                    if first_human_msg:
                        self._last_images_b64.append(current_images)
                        if len(self._last_images_b64) > self.max_history_images:
                            self._last_images_b64.pop(0)

                        msg = msg.copy()
                        all_images_flat = []
                        for img_list in self._last_images_b64:
                            all_images_flat.extend(img_list)

                        msg["images"] = all_images_flat

                        total_imgs = len(all_images_flat)
                        num_frames = len(self._last_images_b64)
                        cam_names_str = ", ".join(self.camera_names) if self.camera_names else "front"

                        if num_frames == 1:
                            msg["content"] += (
                                f"\nAttached {len(current_images)} image(s) from cameras: {cam_names_str}. "
                                "This is the current view of the environment."
                            )
                        else:
                            msg["content"] += (
                                f"\nAttached {total_imgs} images from cameras ({cam_names_str}) over the last {num_frames} steps. "
                                "The latest images represent the current view. "
                                "Previous ones are memory of past states. "
                                "Use ONLY the latest images to assess the current situation! "
                                "Use the others only for change analysis."
                            )
                        first_human_msg = False
                    ollama_messages_with_image_reversed.append(msg)
                else:
                    ollama_messages_with_image_reversed.append(msg)
            ollama_messages = list(reversed(ollama_messages_with_image_reversed))

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {"temperature": self.temperature}
        }

        ## Verbose debug of VLM input (all messages to VLM)
        # pprint(payload, width=80, sort_dicts=False)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{self.base_url}/api/chat", json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise RuntimeError(f"Ollama error {resp.status}: {error_text}")
                    result = await resp.json()
                    response_text = result["message"]["content"]
                    message = AIMessage(content=response_text)
                    generation = ChatGeneration(message=message)
                    return ChatResult(generations=[generation])
            except Exception as e:
                print(f"❌ Error calling Ollama: {e}")
                raise

    def bind_tools(self, tools, **kwargs):
        return self


class AgentNode(Node):
    def __init__(self):
        super().__init__(
            'agent_node',
            allow_undeclared_parameters=True,
            automatically_declare_parameters_from_overrides=True
        )

        self.mcp_server_url = self.get_parameter_or(
            'mcp_server_url', 
            rclpy.parameter.Parameter('mcp_server_url', value='http://127.0.0.1:9000/mcp')
        ).value
        self.ollama_url = self.get_parameter_or(
            'ollama_url',
            rclpy.parameter.Parameter('ollama_url', value='http://ollama:11434')
        ).value
        self.model_name = self.get_parameter_or(
            'model_name',
            rclpy.parameter.Parameter('model_name', value='qwen3-vl:8b-instruct')
        ).value
        self.use_camera_every_tick = self.get_parameter_or(
            'use_camera_every_tick',
            rclpy.parameter.Parameter('use_camera_every_tick', value=False)
        ).value
        self.max_agent_iteration = self.get_parameter_or(
            'max_agent_iteration',
            rclpy.parameter.Parameter('max_agent_iteration', value=150)
        ).value
        self.lang = self.get_parameter_or(
            'lang',
            rclpy.parameter.Parameter('lang', value='ru')
        ).value
        
        # Keep it short for small models otherwise model will confusing
        self.keep_last_tool_messages = self.get_parameter_or(
            'keep_last_tool_messages',
            rclpy.parameter.Parameter('keep_last_tool_messages', value=5)
        ).value

        # Adjust it to your task
        self.specific_system_prompt = self.get_parameter_or(
            'specific_system_prompt',
            rclpy.parameter.Parameter('specific_system_prompt', value=None)
        ).value

        if self.lang not in ('ru', 'en'):
            self.get_logger().warn(f"Invalid 'lang' parameter: '{self.lang}'. Defaulting to 'ru'.")
            self.lang = 'ru'        

        self.active_cameras = self._get_camera_parameters()

        if self.use_camera_every_tick and not self.active_cameras:
            self.get_logger().error(
                "Parameter 'use_camera_every_tick' is set to true, "
                "but no 'camera_*' parameters with non-empty topics are defined."
            )
            raise ValueError("At least one camera topic is required when use_camera_every_tick=True")

        self.tools = []
        self.model = None
        self.is_initialized = False
        self.stop_event = threading.Event()
        self.last_images_b64 = {name: None for name in self.active_cameras}
        self.last_tool_images = []  # <- Storage for images returned by tools
        self.bridge = CvBridge()

        for cam_name, topic in self.active_cameras.items():
            self.create_subscription(
                CompressedImage,
                topic,
                self.make_camera_callback(cam_name),
                10
            )
            self.get_logger().info(f"Subscribed to camera '{cam_name}' on topic: {topic}")

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.run_async_loop, daemon=True)
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.init_mcp_client(), self.loop)

    def _get_camera_parameters(self):
        param_names_result = self.list_parameters([], 100)
        camera_params = {}
        for name in param_names_result.names:
            if name.startswith("camera_"):
                param = self.get_parameter(name)
                if param.type_ != rclpy.parameter.Parameter.Type.NOT_SET:
                    value = param.value
                    if isinstance(value, str) and value.strip():
                        cam_name = name[len("camera_"):]
                        if cam_name:
                            camera_params[cam_name] = value
        return camera_params

    def make_camera_callback(self, cam_name: str):
        def callback(msg: CompressedImage):
            try:
                cv_image = self.bridge.compressed_imgmsg_to_cv2(msg, desired_encoding="rgb8")
                target_width, target_height = 160, 120
                resized_image = cv2.resize(cv_image, (target_width, target_height), interpolation=cv2.INTER_AREA)

                ## save image for test
                # filename = f"./src/{cam_name}.jpg"
                # if not os.path.exists(filename):
                #     cv2.imwrite(filename, cv2.cvtColor(resized_image, cv2.COLOR_RGB2BGR))
                #     self.get_logger().info(f"Saved image to {filename}")
                # else:
                #     self.get_logger().debug(f"Image {filename} already exists, skipping save.")

                _, buffer = cv2.imencode('.jpg', resized_image, [cv2.IMWRITE_JPEG_QUALITY, 70])
                self.last_images_b64[cam_name] = base64.b64encode(buffer).decode('utf-8')
            except Exception as e:
                self.get_logger().error(f"Error in camera_callback ({cam_name}): {e}")
        return callback

    def _extract_images_from_tool_result(self, tool_result) -> List[str]:
        """Extracts base64 images from tool result."""
        images = []
        try:
            if isinstance(tool_result, str):
                data = json.loads(tool_result)
            else:
                data = tool_result

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("type") == "image" and "base64" in item:
                        images.append(item["base64"])
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            pass
        return images

    def get_latest_images_b64(self) -> Optional[List[str]]:
        # Priority: images from tools
        if self.last_tool_images:
            imgs = self.last_tool_images.copy()
            self.last_tool_images.clear()
            return imgs

        # Then — cameras, if enabled
        if self.use_camera_every_tick:
            images = []
            for cam in self.active_cameras:
                img = self.last_images_b64[cam]
                if img is not None:
                    images.append(img)
            return images if images else None

        return None

    def get_active_camera_names(self) -> List[str]:
        return list(self.active_cameras.keys())

    def run_async_loop(self):
        asyncio.set_event_loop(self.loop)

        # activate debug on this thread
        # try:
        #     import debugpy
        #     debugpy.debug_this_thread()
        #     debugpy.trace_this_thread(True)
        # except ImportError:
        #     pass

        self.loop.run_forever()

    async def init_mcp_client(self):
        try:
            client = MultiServerMCPClient({
                "ros_mcp_server": {
                    "url": self.mcp_server_url,
                    "transport": "streamable_http"
                }
            })
            self.tools = await client.get_tools()

            if self.use_camera_every_tick: # exclude analyze_previously_received_image tool
                for i, tool in enumerate(self.tools):
                    if getattr(tool, 'name', None) == 'analyze_previously_received_image':
                        del self.tools[i]
                        break  # del first found               

            tool_names = ", ".join(t.name for t in self.tools)

            SYSTEM_PROMPT = _build_system_prompt(
                lang=self.lang,
                tool_names=tool_names,
                use_camera_every_tick=self.use_camera_every_tick,
                active_camera_names=self.get_active_camera_names(),
                specific_system_prompt_override=self.specific_system_prompt
            )            

            self.model = VisionAwareChatOllama(
                get_images_b64_func=self.get_latest_images_b64,
                model=self.model_name,
                base_url=self.ollama_url,
                temperature=0,
                system=SYSTEM_PROMPT,
                max_history_images=1,
                camera_names=self.get_active_camera_names(),
                keep_last_tool_messages=self.keep_last_tool_messages
            )

            def pull_model_via_api(model_name: str):
                url = f"{self.ollama_url}/api/pull"
                payload = {"name": model_name, "stream": True}
                
                print(f"Downloading model: {model_name}")
                
                try:
                    response = requests.post(url, json=payload, stream=True, timeout=None)
                    response.raise_for_status()

                    total_size = None
                    downloaded_size = 0
                    start_time = time.time()
                    last_update_time = start_time
                    speed_history = [] 
                    HISTORY_LEN = 10 
                    
                    is_complete = False

                    for line in response.iter_lines():
                        if line:
                            json_data = json.loads(line)
                            
                            if 'total' in json_data:
                                total_size = json_data['total']
                            
                            # Check for completion status first
                            if json_data.get('status') == 'success':
                                is_complete = True
                                break  # Exit the loop immediately
                            
                            current_time = time.time()
                            
                            if 'completed' in json_data:
                                downloaded_size = json_data['completed']
                                
                                if total_size and downloaded_size > 0:
                                    if current_time - last_update_time > 0.1: 
                                        # 1. Add to History
                                        speed_history.append({'time': current_time, 'bytes': downloaded_size})
                                        
                                        # 2. Trim history
                                        if len(speed_history) > HISTORY_LEN:
                                            speed_history.pop(0)
                                        
                                        # 3. Calculate Average Speed with FIX
                                        if len(speed_history) >= 2:
                                            window_bytes = speed_history[-1]['bytes'] - speed_history[0]['bytes']
                                            window_time = speed_history[-1]['time'] - speed_history[0]['time']
                                            
                                            avg_speed_bps = 0
                                            # FIX: Only calculate speed if positive bytes transferred
                                            if window_time > 0 and window_bytes > 0:
                                                avg_speed_bps = window_bytes / window_time
                                            
                                            avg_speed_mbps = avg_speed_bps / (1024 * 1024)
                                            
                                            percent = (downloaded_size / total_size) * 100
                                            
                                            print(f"Progress: {percent:.2f}% | Speed: {avg_speed_mbps:.2f} MB/s", end='\r')
                                        
                                        last_update_time = current_time

                    # Print final message cleanly
                    if is_complete:
                        print("\nDownload complete!")
                    else:
                        print("\nDownload finished (stream ended).")
                        
                    return True

                except Exception as e:
                    print(f"\nError during download: {e}")
                    return False

                
            def check_model_exists(model_name: str):
                """
                Checks if a specific model is already available locally in the Ollama instance.
                """
                # Fetch the list of local models
                response = requests.get(f"{self.ollama_url}/api/tags")
                models = response.json().get('models', [])
                
                for m in models:
                    # Ollama returns names like "llama3:latest". 
                    # We check if the requested name is contained in the model name string.
                    if model_name in m['name']:
                        return True
                return False

            def check_and_pull_model(model_name):
                """
                Main logic: Check if model exists, download if missing, then proceed.
                """
                if not check_model_exists(model_name):
                    print(f"Model {model_name} not found. Attempting to download...")
                    success = pull_model_via_api(model_name)
                    
                    if not success:
                        print("Error: Failed to load model")
                        return False
                    
                    # Optional: Verify that the model actually appeared after download
                    if not check_model_exists(model_name):
                        print("Error: Model download command finished, but model is still missing.")
                        return False

                # If we are here, the model exists and is ready.
                print("Model is ready, proceeding with generation request...")
                return True

            if not check_and_pull_model(self.model_name):
                return
                        
            self.is_initialized = True
            await self.handle_user_commands()
        except Exception as e:
            self.get_logger().error(f"Initialization error: {e}")

    async def handle_user_commands(self):
        while not self.stop_event.is_set():
            if not self.is_initialized:
                await asyncio.sleep(1)
                continue
            try:
                user_input = input("\nEnter message: ").strip()
                if not user_input:
                    continue
                if user_input.lower() == 'exit':
                    print("Shutting down...")
                    self.stop_event.set()
                    break
                await self.process_user_input(user_input)
            except Exception as e:
                print(f"Error: {e}")

    async def process_user_input(self, user_input: str):
        messages = [HumanMessage(content=user_input)]
        print(f"\n👤 User: {user_input}\n")
        
        for step in range(self.max_agent_iteration):           
            response = await self.model.ainvoke(messages)
            messages.append(response)
            content = response.content

            print("📤 OLLAMA RESPONSE AND AGENT EXECUTION:")
            print("=" * VISUAL_LINE_LENGTH)
            print(f"----------------------------------------- Message {len(messages)} -----------------------------------------")
            print(f"🧠 Agent: {content}\n")

            if "Action:" in content and "Action Input:" in content:
                try:
                    # Extract action name (up to first newline)
                    action_line = content.split("Action:", 1)[1].split("\n")[0].strip()

                    # Extract everything after "Action Input:"
                    input_part = content.split("Action Input:", 1)[1].strip()

                    # Truncate anything after "Final Answer:" if present
                    if "Final Answer:" in input_part:
                        input_part = input_part.split("Final Answer:", 1)[0].strip()

                    # Remove markdown blocks if present
                    if input_part.startswith("```"):
                        # Find first opening brace {
                        start_idx = input_part.find("{")
                        if start_idx != -1:
                            input_part = input_part[start_idx:]
                        else:
                            # If no {, try [
                            start_idx = input_part.find("[")
                            if start_idx != -1:
                                input_part = input_part[start_idx:]
                            else:
                                input_part = "{}"  # fallback

                    if input_part.endswith("```"):
                        end_idx = input_part.rfind("}")
                        if end_idx != -1:
                            input_part = input_part[:end_idx + 1]
                        else:
                            end_idx = input_part.rfind("]")
                            if end_idx != -1:
                                input_part = input_part[:end_idx + 1]
                            else:
                                input_part = input_part.rstrip("` \n")

                    def fix_unclosed_brackets(json_str):
                        """
                        Attempts to fix a truncated JSON string by adding missing closing brackets/braces.
                        Counts unclosed '{' and '[' and appends the corresponding '}' and ']' at the end.
                        Properly skips characters inside strings to avoid miscounting.
                        """
                        open_braces = 0      # Count of unmatched '{'
                        open_brackets = 0    # Count of unmatched '['

                        i = 0
                        while i < len(json_str):
                            char = json_str[i]
                            if char == '"':
                                # Skip everything inside a string (including escaped quotes)
                                i += 1
                                while i < len(json_str):
                                    if json_str[i] == '\\':
                                        i += 2  # Skip escaped character (e.g., \", \\)
                                    elif json_str[i] == '"':
                                        break   # End of string
                                    else:
                                        i += 1
                            elif char == '{':
                                open_braces += 1
                            elif char == '}':
                                open_braces -= 1
                            elif char == '[':
                                open_brackets += 1
                            elif char == ']':
                                open_brackets -= 1
                            # Ignore all other characters
                            i += 1

                        # Append missing closing brackets: first close arrays, then objects
                        to_add = ']' * open_brackets + '}' * open_braces
                        return json_str + to_add

                    try:
                        tool_input = json.loads(input_part)
                        # print("Successfully parsed JSON: ", tool_input)
                    except json.JSONDecodeError as e:
                        try:
                            print("Broken JSON input to tool:", input_part)
                            print("Trying to fix JSON.")
                            # Fix potential missing closing brackets
                            fixed_input = fix_unclosed_brackets(input_part)     
                            tool_input = json.loads(fixed_input)
                            print("Successfully fixed JSON: ", tool_input)
                            input_part = fixed_input
                        except json.JSONDecodeError as e:         
                            print("Failed to parse JSON even after fixing brackets:", e)
                        
                    # Attempt to parse as JSON
                    tool_input = json.loads(input_part)
                    tool = next((t for t in self.tools if t.name == action_line), None)

                    if tool:
                        tool_result = await tool.ainvoke(tool_input)
                        extracted_images = self._extract_images_from_tool_result(tool_result)

                        messages.append(ToolMessage(
                            content=str(tool_result),
                            name=action_line,
                            tool_call_id=f"call_{step}_{hash(action_line)}",
                        ))
                        # Function to replace base64 with string length
                        def sanitize_tool_result(result):
                            if isinstance(result, list):
                                return [sanitize_tool_result(item) for item in result]
                            elif isinstance(result, dict):
                                sanitized = copy.deepcopy(result)
                                if 'base64' in sanitized and isinstance(sanitized['base64'], str):
                                    length = len(sanitized['base64'])
                                    sanitized['base64'] = f"base64_str_len_{length}"
                                return sanitized
                            else:
                                return result
                        print(f"🔧 Tool '{action_line}' executed: {sanitize_tool_result(tool_result)}\n")

                        if extracted_images:
                            self.last_tool_images = extracted_images
                            print(f"🖼️ Received {len(extracted_images)} image(s) from tool. Will be used in next step as 'front'.\n")
                    else:
                        err = f"Tool '{action_line}' not found."
                        messages.append(ToolMessage(content=err, name="error", tool_call_id=f"call_{step}_{hash(action_line)}"))
                        print(f"⚠️ {err}\n")
                        # break
                except Exception as e:
                    err = 'Parsing error (maybe bad structured json, check count of open/close curles brakets).\n ' \
                    + f'You sent tool input: {input_part}.\n ' + f'\nError: {str(e)}'
                    messages.append(ToolMessage(content=err, name="error", tool_call_id=f"call_{step}_{hash(action_line)}"))
                    print(f"❌ {err}\n")
                    # break because model forever cyclic in that case
                    break
            else:
                print("ℹ️ Agent did not request an action. Terminating. \n" + f'Response content is: {content}')
                break

            if "Final Answer:" in content and not self.last_tool_images:
                final_ans = content.split("Final Answer:", 1)[-1].strip()
                print(f"✅ FINAL ANSWER: {final_ans}\n")
                break

        print("\n" + "="*VISUAL_LINE_LENGTH)
        print("🔄 HISTORY:")
        for msg in messages:
            role = "👤" if isinstance(msg, HumanMessage) else \
                   "🧠" if isinstance(msg, AIMessage) else \
                   f"🔧 ({getattr(msg, 'name', 'N/A')})"
            cont = str(msg.content).replace('\n', ' ')[:120]
            print(f"{role} {cont}")
        print("="*VISUAL_LINE_LENGTH + "\n")

    def destroy_node(self):
        self.stop_event.set()

        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        # Unblock input and clear terminal
        try:
            readline.insert_text("exit")
            readline.redisplay()
            os.write(sys.stdin.fileno(), b'\n')
        except Exception:
            pass
        
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)

        # RESTORE TERMINAL
        # os.system('stty sane') resets terminal settings to standard modes,
        # fixing the issue with "invisible" input after the script finishes.
        os.system('stty sane')

        super().destroy_node()


def main(args=None):
    # Initialize ROS
    rclpy.init(args=args)
    node = AgentNode()

    # Flag to prevent re-entry into the handler
    shutdown_requested = False

    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        
        shutdown_requested = True
        print("\nCtrl+C detected. Shutting down...")
        
        try:
            node.destroy_node()
        except Exception as e:
            print(f"Error during node destruction: {e}")
        
        try:
            rclpy.shutdown()
        except Exception:
            pass
        
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception:
        pass
    finally:
        if not shutdown_requested:
            try:
                node.destroy_node()
            except Exception:
                pass
            try:
                rclpy.shutdown()
            except Exception:
                pass


if __name__ == '__main__':
    main()
