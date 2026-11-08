import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import hashlib


def pad_key(key: str) -> bytes:
    """
    填充密钥到指定长度（16、24或32位）
    @param key: 原始密钥字符串
    @return: 填充后的密钥字节数组
    """
    key_bytes = key.encode("utf-8")
    key_length = len(key_bytes)

    if key_length == 16 or key_length == 24 or key_length == 32:
        return key_bytes

    # 如果密钥长度不足，用0填充；如果超过，截取前32位
    padded_key = bytearray(32)
    padded_key[: min(key_length, 32)] = key_bytes[: min(key_length, 32)]
    return bytes(padded_key)


def encrypt(key: str, plain_text: str) -> str:
    """
    AES加密
    @param key: 密钥（16位、24位或32位）
    @param plain_text: 待加密字符串
    @return: 加密后的Base64字符串
    """
    try:
        # 确保密钥长度为16、24或32位
        key_bytes = pad_key(key)
        cipher = AES.new(key_bytes, AES.MODE_ECB)

        # 对明文进行PKCS7填充
        try:
            plain_bytes = plain_text.encode("utf-8")
            padded_data = pad(plain_bytes, AES.block_size)
        except Exception as e:
            raise ValueError(f"明文编码或填充失败: {str(e)}")

        # 加密
        try:
            encrypted_bytes = cipher.encrypt(padded_data)
        except Exception as e:
            raise ValueError(f"加密失败: {str(e)}")

        # Base64编码
        try:
            return base64.b64encode(encrypted_bytes).decode("utf-8")
        except Exception as e:
            raise ValueError(f"Base64编码失败: {str(e)}")
    except ValueError as e:
        # 重新抛出ValueError，保持错误类型一致
        raise e
    except Exception as e:
        raise RuntimeError(f"加密过程中发生未知错误: {str(e)}")


def decrypt(key: str, encrypted_text: str) -> str:
    """
    AES解密
    @param key: 密钥（16位、24位或32位）
    @param encrypted_text: 待解密的Base64字符串
    @return: 解密后的字符串
    """
    try:
        # 确保密钥长度为16、24或32位
        key_bytes = pad_key(key)
        cipher = AES.new(key_bytes, AES.MODE_ECB)

        # 解码Base64
        try:
            encrypted_bytes = base64.b64decode(encrypted_text)
        except Exception as e:
            return None

        # 解密
        try:
            decrypted_bytes = cipher.decrypt(encrypted_bytes)
        except Exception as e:
            return None

        # 去除PKCS7填充
        try:
            unpadded_data = unpad(decrypted_bytes, AES.block_size)
        except Exception as e:
            return None

        return unpadded_data.decode("utf-8")
    except ValueError as e:
        # 重新抛出ValueError，保持错误类型一致
        return None
    except Exception as e:
        return None


if __name__ == "__main__":
    # 测试代码
    test_key = "6a369b7f1bcf4d3e8d123ece38bb9627"
    test_text = '{"agentId": "test1"}'

    print(f"原始文本: {test_text}")
    print(f"密钥: {test_key}")

    # 加密
    encrypted = encrypt(test_key, test_text)
    print(f"加密结果: {encrypted}")

    # 解密
    decrypted = decrypt(test_key, encrypted)
    print(f"解密结果: {decrypted}")

    # 验证
    print(f"加解密一致性: {test_text == decrypted}")
