from zhipuai import ZhipuAI
from env import get_env_value

Image_generate_client = ZhipuAI(api_key=get_env_value("IMAGE_GENERATE_API"))
Image_describe_client=ZhipuAI(api_key=get_env_value("IMAGE_DESCRIBE_API"))

