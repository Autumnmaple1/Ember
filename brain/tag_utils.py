"""
标签处理工具 - 修复和处理 <thought> 标签的完整性问题
"""
import re
import logging

logger = logging.getLogger(__name__)


def fix_thought_tags(text: str) -> str:
    """
    修复不完整的 <thought> 标签
    
    常见问题：
    - </thought` (多了一个反引号)
    - </thought (缺少 >)
    - 只有 <thought> 没有 </thought>
    - 只有 </thought> 没有 <thought>
    
    Args:
        text: 原始文本
        
    Returns:
        修复后的文本
    """
    if not text:
        return text
    
    original_text = text
    
    # 1. 修复常见的错误格式
    # 修复 </thought` 或 </thought``
    text = re.sub(r'</thought[`\'"]+', '</thought>', text)
    
    # 修复 </thought 缺少 >
    text = re.sub(r'</thought([^>])', r'</thought>\1', text)
    text = re.sub(r'</thought$', '</thought>', text)
    
    # 2. 检查标签配对
    open_tags = text.count('<thought>')
    close_tags = text.count('</thought>')
    
    # 如果有未闭合的 <thought>
    if open_tags > close_tags:
        # 在最后一个 <thought> 之后的位置添加闭合标签
        last_open = text.rfind('<thought>')
        if last_open != -1:
            # 尝试找到合适的插入位置（在下一个空行或文本末尾）
            remaining = text[last_open:]
            
            # 如果在 <thought> 之后没有找到 </thought>
            if '</thought>' not in remaining:
                # 找到下一个空行或双换行
                next_para = re.search(r'\n\s*\n', remaining)
                if next_para:
                    insert_pos = last_open + next_para.start()
                    text = text[:insert_pos] + '\n</thought>' + text[insert_pos:]
                else:
                    # 在文本末尾添加
                    text = text.rstrip() + '\n</thought>'
        logger.warning(f"修复了未闭合的 <thought> 标签")
    
    # 如果有未开启的 </thought>
    elif close_tags > open_tags:
        # 在第一个 </thought> 之前添加开启标签
        first_close = text.find('</thought>')
        if first_close != -1:
            # 找到前一个段落开始位置
            prev_para = text.rfind('\n\n', 0, first_close)
            if prev_para == -1:
                prev_para = 0
            else:
                prev_para += 2
            
            text = text[:prev_para] + '<thought>\n' + text[prev_para:]
        logger.warning(f"修复了未开启的 </thought> 标签")
    
    if text != original_text:
        logger.info(f"标签修复前: {original_text[:100]}...")
        logger.info(f"标签修复后: {text[:100]}...")
    
    return text


def remove_thought_content(text: str) -> str:
    """
    移除 <thought>...</thought> 标签及其内容
    支持不完整的标签（容错处理）
    
    Args:
        text: 原始文本
        
    Returns:
        移除 thought 内容后的文本
    """
    if not text:
        return text
    
    # 先修复标签
    text = fix_thought_tags(text)
    
    # 移除完整的 thought 块
    text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL)
    
    # 清理可能残留的不完整标签
    text = re.sub(r'</?thought[^>]*>', '', text)
    
    # 清理多余的空行
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    text = text.strip()
    
    return text


def extract_thought_and_speech(text: str) -> tuple[str, str]:
    """
    分离 thought 内容和 speech 内容
    
    Args:
        text: 原始文本
        
    Returns:
        (thought_content, speech_content)
    """
    if not text:
        return "", ""
    
    # 先修复标签
    text = fix_thought_tags(text)
    
    # 提取 thought 内容
    thought_match = re.search(r'<thought>([\s\S]*?)</thought>', text)
    thought = thought_match.group(1).strip() if thought_match else ""
    
    # 提取 speech 内容（移除 thought 部分）
    speech = remove_thought_content(text)
    
    return thought, speech


def validate_and_fix_llm_output(text: str) -> str:
    """
    验证并修复 LLM 输出的格式问题
    主要用于在保存到数据库前进行格式校验
    
    Args:
        text: LLM 输出的原始文本
        
    Returns:
        修复后的文本
    """
    if not text:
        return text
    
    # 修复 thought 标签
    text = fix_thought_tags(text)
    
    # 清理其他可能的格式问题
    # 移除多余的反引号
    text = re.sub(r'```\s*', '', text)
    
    # 确保不会有多余的空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()
